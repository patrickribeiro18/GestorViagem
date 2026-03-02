"""
services/route_optimizer.py
Lógica pura de otimização de paradas de viagem.
Extraído de main.py para facilitar manutenção e permitir testes unitários.
"""
from datetime import datetime, timedelta
import utils


def get_coords_at_km(geo: list, target_km: float) -> tuple[float, float, float]:
    """
    Retorna (lat, lon, index_na_geometria) mais próximos do KM desejado 
    calculando a distância cumulativa.
    """
    total_dist = 0.0
    for i in range(len(geo) - 1):
        p1 = geo[i]
        p2 = geo[i + 1]
        # utils.calculate_distance espera (lat1, lon1, lat2, lon2)
        d = utils.calculate_distance(p1[1], p1[0], p2[1], p2[0])
        if total_dist + d >= target_km:
            # Interpolação simples ou apenas retorna o ponto mais próximo
            return p2[1], p2[0], i + 1
        total_dist += d
    return geo[-1][1], geo[-1][0], len(geo) - 1


def optimize_stops(
    geo: list,
    total_km: float,
    avg_speed_kmh: float,
    start_simulation_dt: datetime,
    points_labels: list,
    points_coords: list,
    tank: float,
    kml: float,
    dur_fuel: float,
    dur_rest: float,
    dur_sleep: float,
    max_drive_hours: float,
    rest_interval_km: float,
    avoid_night: bool,
    extend_sunset: bool,
    ors_client,
    progress_callback=None,
) -> tuple[list, float]:
    """
    Calcula a lista bruta de paradas e retorna (raw_timeline, accumulated_hours).
    """
    raw_timeline = []

    d_rest = 0.0
    d_fuel = 0.0
    d_sleep = 0.0

    range_fuel = tank * kml * 0.90
    limit_sleep = max_drive_hours * avg_speed_kmh
    hard_limit_sleep = limit_sleep * 1.5

    check_step = 10
    day_count = 1
    lookahead_km = 100 # Reduzido para ser mais agressivo no combo
    accumulated_hours = 0.0

    raw_timeline.append({
        "km": 0,
        "type": "start",
        "name": "Início",
        "place": points_labels[0],
        "addr": "-",
        "duration_h": 0,
        "arrival_offset_h": 0,
        "coords": [points_coords[0][1], points_coords[0][0]],
    })

    km_now = 0.0
    while km_now < (total_km - 15): # 15km de margem para o destino final
        km_now += check_step
        if progress_callback:
            progress_callback(min(km_now / total_km, 1.0), f"Analisando km {int(km_now)}…")

        hours_step = check_step / avg_speed_kmh
        accumulated_hours += hours_step
        current_sim_dt = start_simulation_dt + timedelta(hours=accumulated_hours)
        
        # Inteligência Solar/Noturna
        is_night_now = current_sim_dt.hour >= 20 or current_sim_dt.hour < 4
        minutes_to_night = 999
        if current_sim_dt.hour < 20:
             # Horas restantes até 20:00
             minutes_to_night = ((current_sim_dt.replace(hour=20, minute=0) - current_sim_dt).total_seconds() / 60)

        d_rest += check_step
        d_fuel += check_step
        d_sleep += check_step

        lat, lon, _ = get_coords_at_km(geo, km_now)

        cat = None
        reason = ""
        stop_duration_h = 0

        effective_sleep_limit = limit_sleep
        if extend_sunset and (not is_night_now) and (d_sleep < hard_limit_sleep):
            effective_sleep_limit = hard_limit_sleep

        # --- REGRAS DE COMBO E ANTECIPAÇÃO ---
        
        # 1. Pernoite Forçado (Anoiteceu ou bateu limite de horas)
        forced_night = (avoid_night and is_night_now) or (d_sleep >= effective_sleep_limit)
        
        # 2. Se falta pouco para pernoite/noite, checa se precisa abastecer/descansar agora
        near_night = (avoid_night and minutes_to_night < 90) or (effective_sleep_limit - d_sleep < lookahead_km)
        
        if forced_night:
            cat = "night_stop"
            reason = "🛑 Pernoite"
            if d_fuel > (range_fuel * 0.7):
                cat = "combo"
                reason = "🛌 Pernoite + Abastecer"
                d_fuel = 0
            
            # Reset de cansaço
            stop_duration_h = dur_sleep
            d_sleep = 0
            d_rest = 0
            day_count += 1

        elif d_fuel >= range_fuel:
            # Se for abastecer e estiver perto da noite, faz combo pernoite
            if near_night:
                cat = "combo"
                reason = "⛽ Abastecer + Pernoite"
                stop_duration_h = dur_sleep
                d_sleep = 0
                day_count += 1
            else:
                cat = "fuel"
                reason = "Fuel Stop"
                stop_duration_h = dur_fuel / 60
            d_fuel = 0
            d_rest = 0

        elif d_rest >= rest_interval_km:
            # Se for descansar e estiver perto de algo maior (noite ou combustível), decide se antecipa
            km_rem_fuel = range_fuel - d_fuel
            if near_night:
                # Antecipa pernoite em vez de só café
                cat = "night_stop"
                reason = "🛑 Antecipando Pernoite (Fadiga/Noite)"
                stop_duration_h = dur_sleep
                d_sleep = 0
                day_count += 1
            elif km_rem_fuel < lookahead_km:
                cat = "fuel"
                reason = "☕ Pausa + Abastecer"
                stop_duration_h = dur_fuel / 60
                d_fuel = 0
            else:
                cat = "coffee"
                reason = "Pausa Descanso"
                stop_duration_h = dur_rest / 60
            d_rest = 0

        if cat:
            # Ajuste noturno (não sai antes das 06h se avoid_night estiver ativo)
            tentative_dep = current_sim_dt + timedelta(hours=stop_duration_h)
            if avoid_night and (tentative_dep.hour >= 20 or tentative_dep.hour < 4):
                if tentative_dep.hour >= 20:
                    nm = (tentative_dep + timedelta(days=1)).replace(hour=6, minute=0, second=0)
                else:
                    nm = tentative_dep.replace(hour=6, minute=0, second=0)
                new_dur = (nm - current_sim_dt).total_seconds() / 3600
                stop_duration_h = new_dur
                if "Pernoite" not in reason:
                    reason += " + Esperar Amanhecer"
                    d_sleep = 0

            real_coords, popup, color, icon, s_name, s_addr = utils.find_best_place_google(
                lat, lon, cat, ors_client
            )
            raw_timeline.append({
                "km": int(km_now),
                "type": "stop",
                "name": reason,
                "place": s_name,
                "addr": s_addr,
                "duration_h": stop_duration_h,
                "arrival_offset_h": accumulated_hours,
                "coords": real_coords,
                "popup": popup,
                "color": color,
                "icon": icon,
            })
            accumulated_hours += stop_duration_h

    # Adiciona Chegada Final se não houver um stop exatamente lá
    raw_timeline.append({
        "km": int(total_km),
        "type": "end",
        "name": "Chegada Final",
        "place": points_labels[-1],
        "addr": "-",
        "duration_h": 0,
        "arrival_offset_h": accumulated_hours,
        "coords": [points_coords[-1][1], points_coords[-1][0]],
    })

    return raw_timeline, accumulated_hours

    raw_timeline.append({
        "km": int(total_km),
        "type": "end",
        "name": "Chegada Final",
        "place": points_labels[-1],
        "addr": "-",
        "duration_h": 0,
        "arrival_offset_h": accumulated_hours,
        "coords": [points_coords[-1][1], points_coords[-1][0]],
    })

    return raw_timeline, accumulated_hours


def build_segment_table(raw_timeline: list, start_dt: datetime) -> list:
    """
    Converte raw_timeline numa lista de dicionários prontos para DataFrame.
    """
    segment_table = []
    for i in range(len(raw_timeline) - 1):
        start_p = raw_timeline[i]
        end_p = raw_timeline[i + 1]

        dt_dep_A = start_dt + timedelta(
            hours=start_p["arrival_offset_h"] + start_p["duration_h"]
        )
        dt_arr_B = start_dt + timedelta(hours=end_p["arrival_offset_h"])

        name_A = f"🏠 {start_p['place']}" if start_p["type"] == "start" else start_p["place"]
        name_B = f"🏁 {end_p['place']}" if end_p["type"] == "end" else end_p["place"]
        event_B = "Chegada Final" if end_p["type"] == "end" else end_p["name"]
        dist_seg = end_p["km"] - start_p["km"]

        segment_table.append({
            "Etapa": i + 1,
            "Saindo de…": name_A,
            "Dia/Hora Saída": dt_dep_A.strftime("%d/%m %H:%M"),
            "Indo para…": name_B,
            "Dia/Hora Chegada": dt_arr_B.strftime("%d/%m %H:%M"),
            "Distância": f"{int(dist_seg)} km",
            "Atividade": event_B,
            "Endereço": end_p.get("addr", "-"),
        })

    return segment_table
