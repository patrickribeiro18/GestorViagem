import streamlit as st
import openrouteservice
import folium
from streamlit_folium import st_folium
import pandas as pd
import time
from datetime import datetime, timedelta
import extra_streamlit_components as stx
import utils
from services.route_optimizer import optimize_stops, build_segment_table

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Roteiro Seguro Pro", layout="wide", page_icon="🚐")

# --- CONSTANTES ---
TIMEOUT_MINUTES = 10

# --- GERENCIADOR DE COOKIES ---
cookie_manager = stx.CookieManager()


# --- FUNÇÕES DE CONTROLE DE SESSÃO ---

def refresh_session_cookie(session):
    """Renova a validade do cookie por mais 10 minutos."""
    expires = datetime.now() + timedelta(minutes=TIMEOUT_MINUTES)
    # Usamos keys únicas para evitar StreamlitDuplicateElementKey
    cookie_manager.set("sb_access_token", session.access_token, expires_at=expires, key="set_access")
    cookie_manager.set("sb_refresh_token", session.refresh_token, expires_at=expires, key="set_refresh")


def logout():
    """Limpa tudo e desloga."""
    utils.logout_user()
    
    # Verifica se os cookies existem antes de deletar para evitar KeyError no componente
    # Usamos keys únicas para cada operação de delete
    try:
        if cookie_manager.get("sb_access_token"):
            cookie_manager.delete("sb_access_token", key="del_access")
        if cookie_manager.get("sb_refresh_token"):
            cookie_manager.delete("sb_refresh_token", key="del_refresh")
    except Exception:
        pass

    st.session_state["session"] = None
    st.session_state["user"] = None
    st.session_state["trip_result"] = None
    # Também limpamos o estado do Google para garantir um novo fluxo limpo
    if "google_auth_url" in st.session_state: del st.session_state["google_auth_url"]
    if "google_verifier" in st.session_state: del st.session_state["google_verifier"]
    
    st.rerun()


# --- 1. VERIFICAÇÃO DE INATIVIDADE (TIMEOUT) ---
if "last_active" not in st.session_state:
    st.session_state["last_active"] = datetime.now()

if st.session_state.get("user"):
    now = datetime.now()
    diff = (now - st.session_state["last_active"]).total_seconds()
    if diff > (TIMEOUT_MINUTES * 60):
        st.warning(f"Sessão expirada após {TIMEOUT_MINUTES} min de inatividade.")
        time.sleep(2)
        logout()
    else:
        st.session_state["last_active"] = now


# --- 2. TENTATIVA DE RESTAURAR SESSÃO (COOKIES) ---
if not st.session_state.get("user"):
    try:
        # Usamos keys únicas para cada chamada de get
        c_access = cookie_manager.get("sb_access_token", key="get_access")
        c_refresh = cookie_manager.get("sb_refresh_token", key="get_refresh")
        
        if c_access and c_refresh:
            session_restored, err = utils.restore_session(c_access, c_refresh)
            if session_restored:
                st.session_state["session"] = session_restored
                st.session_state["user"] = session_restored.user
                st.session_state["last_active"] = datetime.now()
                time.sleep(0.5)
                st.rerun()
    except Exception:
        pass


# --- 3. LOGIN VIA URL (GOOGLE) ---
query_params = st.query_params
if "code" in query_params:
    auth_code = query_params["code"]
    st.info("🔄 Finalizando login com Google...")
    
    # Recupera o verifier que salvamos quando o botão foi gerado
    verifier = st.session_state.get("google_verifier")
    
    session, err = utils.exchange_code_for_session(auth_code, code_verifier=verifier)
    
    if session:
        st.session_state["session"] = session
        st.session_state["user"] = session.user
        st.session_state["last_active"] = datetime.now()
        refresh_session_cookie(session)
        st.success("✅ Login Google realizado com sucesso!")
        st.query_params.clear()
        # Limpa o verifier e a URL gerada
        if "google_verifier" in st.session_state: del st.session_state["google_verifier"]
        if "google_auth_url" in st.session_state: del st.session_state["google_auth_url"]
        time.sleep(1.5)
        st.rerun()
    else:
        # Se falhou, mostramos o erro e limpamos o estado para permitir nova tentativa
        st.error(f"❌ Falha no login Google: {err}")
        if "google_verifier" in st.session_state: 
            st.warning("Dica: O verifier PKCE estava presente. O erro pode ser de expiração do código ou redirecionamento.")
        else:
            st.warning("Dica: O verifier PKCE NÃO foi encontrado na sessão. Certifique-se de estar na mesma aba.")
        
        st.query_params.clear()
        if "google_verifier" in st.session_state: del st.session_state["google_verifier"]
        if "google_auth_url" in st.session_state: del st.session_state["google_auth_url"]
        time.sleep(5)
        st.rerun()

# --- ESTADO INICIAL GERAL ---
if "trip_result" not in st.session_state:
    st.session_state["trip_result"] = None


# --- SIDEBAR ---
with st.sidebar:
    st.title("🔐 Acesso")

    if st.session_state.get("user"):
        time_left = (TIMEOUT_MINUTES * 60) - (
            datetime.now() - st.session_state["last_active"]
        ).total_seconds()
        if time_left > 0:
            st.caption(f"⏱️ Sessão expira em: {int(time_left / 60)} min")

    if st.session_state.get("user"):
        email_display = st.session_state["user"].email or "Viajante"
        st.success(f"Logado: {email_display}")
        if st.button("Sair", type="primary"):
            logout()
    else:
        st.subheader("Entrar")
        
        # Gera a URL de auth apenas se não tivermos uma válida na sessão
        # Isso evita invalidar o flow state a cada rerun da sidebar
        if "google_auth_url" not in st.session_state:
            auth_data = utils.get_google_auth_data()
            if auth_data:
                st.session_state["google_auth_url"] = auth_data["url"]
                st.session_state["google_verifier"] = auth_data["code_verifier"]
        
        google_url = st.session_state.get("google_auth_url")
        if google_url:
            # st.link_button sempre abre em nova aba. Usamos markdown para forçar target="_self"
            # e manter o session_state (google_verifier) na mesma aba.
            st.markdown(
                f"""
                <a href="{google_url}" target="_self" style="
                    text-decoration: none;
                    color: white;
                    background-color: #2e7d32;
                    padding: 10px 20px;
                    border-radius: 5px;
                    display: block;
                    text-align: center;
                    font-weight: bold;
                    margin-bottom: 20px;
                ">🔵 Entrar com Google</a>
                """,
                unsafe_allow_html=True
            )
            st.markdown("---")

        tab1, tab2 = st.tabs(["Email", "Criar Conta"])
        with tab1:
            e = st.text_input("Email")
            p = st.text_input("Senha", type="password")
            if st.button("Entrar"):
                session, err = utils.login_user(e, p)
                if session:
                    st.session_state["session"] = session
                    st.session_state["user"] = session.user
                    st.session_state["last_active"] = datetime.now()
                    refresh_session_cookie(session)
                    st.rerun()
                else:
                    st.error(f"Erro: {err}")
        with tab2:
            e2 = st.text_input("Novo Email")
            p2 = st.text_input("Nova Senha", type="password")
            if st.button("Cadastrar"):
                session, err = utils.signup_user(e2, p2)
                if session:
                    st.session_state["user"] = session.user
                    st.session_state["last_active"] = datetime.now()
                    refresh_session_cookie(session)
                    st.success("Conta criada! Entrando...")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(f"Erro: {err}")


# --- BLOQUEIO TOTAL ---
if not st.session_state.get("user"):
    st.info("👋 Bem-vindo ao Planejador de Viagem!")
    st.warning(
        "🔒 Faça login para começar. A sessão expira automaticamente após "
        f"{TIMEOUT_MINUTES} minutos de inatividade."
    )
    st.stop()


# ==============================================================================
# ÁREA LOGADA - APLICAÇÃO PRINCIPAL
# ==============================================================================

st.title("🗺️ Planejador de Viagem")
tab_planner, tab_community = st.tabs(["🗺️ Planejar Nova Viagem", "🌍 Mural da Comunidade"])


# ==============================================================================
# ABA 1: PLANEJADOR
# ==============================================================================
with tab_planner:

    # --- ESTADO DOS PONTOS NO MAPA ---
    if "origin_data" not in st.session_state: st.session_state["origin_data"] = None
    if "dest_data" not in st.session_state: st.session_state["dest_data"] = None
    if "waypoints" not in st.session_state: st.session_state["waypoints"] = []
    if "stored_now" not in st.session_state: st.session_state["stored_now"] = datetime.now()

    st.subheader("📍 Seleção de Rota")
    st.info("Clique no mapa para definir: 1º Origem (🟢), 2º Destino (🏁) e depois até 5 Waypoints (🔵).")

    # MAPA INTERATIVO INICIAL
    m_init = folium.Map(location=[-15, -50], zoom_start=4)
    
    # Adiciona marcadores existentes
    if st.session_state["origin_data"]:
        folium.Marker(st.session_state["origin_data"]["coords"][::-1], tooltip="Origem", icon=folium.Icon(color="green")).add_to(m_init)
    if st.session_state["dest_data"]:
        folium.Marker(st.session_state["dest_data"]["coords"][::-1], tooltip="Destino", icon=folium.Icon(color="black", icon="flag")).add_to(m_init)
    for i, wp in enumerate(st.session_state["waypoints"]):
        folium.Marker(wp["coords"][::-1], tooltip=f"Waypoint {i+1}", icon=folium.Icon(color="blue", icon="info-sign")).add_to(m_init)

    # Captura clique
    map_data = st_folium(m_init, use_container_width=True, height=400, key="selection_map")

    if map_data and map_data.get("last_clicked"):
        clicked_coords = [map_data["last_clicked"]["lng"], map_data["last_clicked"]["lat"]]
        
        # Lógica de atribuição automática por ordem de clique
        if not st.session_state["origin_data"]:
            st.session_state["origin_data"] = {"label": "Ponto no Mapa", "coords": clicked_coords}
            st.rerun()
        elif not st.session_state["dest_data"]:
            st.session_state["dest_data"] = {"label": "Ponto no Mapa", "coords": clicked_coords}
            st.rerun()
        elif len(st.session_state["waypoints"]) < 5:
            # Verifica se não clicou no mesmo local do destino (evita loops)
            dist_dest = utils.calculate_distance(clicked_coords[1], clicked_coords[0], st.session_state["dest_data"]["coords"][1], st.session_state["dest_data"]["coords"][0])
            if dist_dest > 0.01:
                st.session_state["waypoints"].append({"label": f"Waypoint {len(st.session_state['waypoints'])+1}", "coords": clicked_coords})
                st.rerun()

    c_reset1, c_reset2, c_reset3 = st.columns(3)
    if c_reset1.button("🗑️ Limpar Origem"):
        st.session_state["origin_data"] = None
        st.session_state["trip_result"] = None
        st.rerun()
    if c_reset2.button("🏁 Limpar Destino"):
        st.session_state["dest_data"] = None
        st.session_state["trip_result"] = None
        st.rerun()
    if c_reset3.button("🔵 Limpar Waypoints"):
        st.session_state["waypoints"] = []
        st.session_state["trip_result"] = None
        st.rerun()

    st.divider()

    # BUSCA POR TEXTO (Sincronizada)
    c1, c2 = st.columns(2)
    with c1:
        search_origin = st.text_input("Buscar Origem (Cidade, Endereço ou Lat,Lon):", placeholder="Ex: São Luís", key="txt_o")
        if search_origin:
            opts = utils.search_location_options(search_origin)
            if opts:
                sel = st.selectbox("Selecione Origem:", opts, format_func=lambda x: x[0], key="sel_o")
                if st.button("Definir como Origem", key="btn_o"):
                    st.session_state["origin_data"] = {"label": sel[0], "coords": sel[1]}
                    st.rerun()
        if st.session_state["origin_data"]:
            st.success(f"Origem: {st.session_state['origin_data']['label']}")
            
    with c2:
        search_dest = st.text_input("Buscar Destino (Cidade, Endereço ou Lat,Lon):", placeholder="Ex: Teresina", key="txt_d")
        if search_dest:
            opts = utils.search_location_options(search_dest)
            if opts:
                sel = st.selectbox("Selecione Destino:", opts, format_func=lambda x: x[0], key="sel_d")
                if st.button("Definir como Destino", key="btn_d"):
                    st.session_state["dest_data"] = {"label": sel[0], "coords": sel[1]}
                    st.rerun()
        if st.session_state["dest_data"]:
            st.success(f"Destino: {st.session_state['dest_data']['label']}")

    if st.session_state["waypoints"]:
        st.info(f"Waypoints ativos: {len(st.session_state['waypoints'])} (Máx: 5)")

    st.divider()

    # ----- FORMULÁRIO DE CONFIGURAÇÃO -----
    with st.form("trip_form"):
        c_time1, c_time2, c_time3 = st.columns([1, 1, 2])
        with c_time1:
            plan_mode = st.radio("Modo:", ["Sair às...", "Chegar às..."])
            st.caption("🧠 Inteligência")
            avoid_night = st.checkbox("🚫 Evitar noite (20h–04h)", value=True)
            extend_sunset = st.checkbox("🌅 Estender até anoitecer", value=True)
        with c_time2:
            input_date = st.date_input("Data", value=st.session_state["stored_now"].date())
            input_time = st.time_input("Horário", value=st.session_state["stored_now"].time())
        with c_time3:
            st.caption("⏳ Durações")
            cc1, cc2, cc3 = st.columns(3)
            dur_fuel = cc1.number_input("Abastecer (min)", value=20, min_value=5)
            dur_rest = cc2.number_input("Descanso (min)", value=30, min_value=5)
            dur_sleep = cc3.number_input("Pernoite (h)", value=8, min_value=4)

        with st.expander("⚙️ Veículo e Limites", expanded=False):
            c3, c4, c5 = st.columns(3)
            kml = c3.number_input("Km/L", value=10, min_value=1)
            tank = c4.number_input("Tanque (L)", value=50, min_value=10)
            price = c5.number_input("Preço Combustível (R$)", value=5.89, step=0.01)
            c6, c7 = st.columns(2)
            max_drive_hours = c6.slider("Meta Horas/Dia", 4, 14, 9)
            rest_interval_km = c7.slider("Pausa Descanso (Km)", 150, 400, 250)

        submitted = st.form_submit_button(
            "🗺️ Gerar Cronograma",
            type="primary",
            disabled=(not st.session_state["origin_data"] or not st.session_state["dest_data"]),
            use_container_width=True,
        )

    # ----- PROCESSAMENTO DA ROTA -----
    if submitted and st.session_state["origin_data"] and st.session_state["dest_data"]:
        user_datetime = datetime.combine(input_date, input_time)
        client = openrouteservice.Client(key=utils.ORS_KEY)
        try:
            with st.status("⚙️ Calculando rota...", expanded=True) as status:
                st.write("📡 Buscando rota otimizada...")
                
                # Monta lista de coordenadas pro ORS: [Origin, W1, ..., W5, Dest]
                all_pts_coords = [st.session_state["origin_data"]["coords"]]
                all_pts_labels = [st.session_state["origin_data"]["label"]]
                for wp in st.session_state["waypoints"]:
                    all_pts_coords.append(wp["coords"])
                    all_pts_labels.append(wp["label"])
                all_pts_coords.append(st.session_state["dest_data"]["coords"])
                all_pts_labels.append(st.session_state["dest_data"]["label"])

                # Chama API com todos os pontos
                route = client.directions(
                    coordinates=all_pts_coords,
                    profile="driving-car",
                    format="geojson"
                )
                
                geo = route["features"][0]["geometry"]["coordinates"]
                summary = route["features"][0]["properties"]["summary"]
                total_km = summary["distance"] / 1000
                avg_speed_kmh = total_km / (summary["duration"] / 3600)

                if plan_mode == "Sair às...":
                    start_simulation_dt = user_datetime
                else:
                    start_simulation_dt = user_datetime - timedelta(hours=(total_km / avg_speed_kmh) * 1.2)

                st.write("🧮 Otimizando paradas...")
                prog = st.progress(0)
                def _progress(frac, text): prog.progress(frac, text=text)

                raw_timeline, accumulated_hours = optimize_stops(
                    geo=geo, total_km=total_km, avg_speed_kmh=avg_speed_kmh,
                    start_simulation_dt=start_simulation_dt,
                    points_labels=all_pts_labels, points_coords=all_pts_coords,
                    tank=tank, kml=kml, dur_fuel=dur_fuel, dur_rest=dur_rest, dur_sleep=dur_sleep,
                    max_drive_hours=max_drive_hours, rest_interval_km=rest_interval_km,
                    avoid_night=avoid_night, extend_sunset=extend_sunset,
                    ors_client=client, progress_callback=_progress
                )
                prog.empty()

                st.write("📋 Montando roteiro...")
                segment_table = build_segment_table(raw_timeline, start_simulation_dt)

                # Marcadores para o mapa de resultado e lista de pontos para REFINAMENTO da rota
                markers = []
                refinement_coords = [[st.session_state["origin_data"]["coords"][0], st.session_state["origin_data"]["coords"][1]]]
                
                for entry in raw_timeline:
                    if entry["type"] == "stop":
                        markers.append({
                            "loc": entry["coords"],
                            "popup": f"<b>{entry['name']}</b><br>{entry['place']}",
                            "color": entry["color"], "icon": entry["icon"]
                        })
                        # Adiciona a coordenada real da parada para o refinamento
                        refinement_coords.append([entry["coords"][1], entry["coords"][0]])
                
                refinement_coords.append([st.session_state["dest_data"]["coords"][0], st.session_state["dest_data"]["coords"][1]])

                # REFINAMENTO: Recalcula a geometria exata passando pelas paradas
                final_geo = geo
                if len(refinement_coords) > 2:
                    st.write("🔄 Refinando traçado para incluir paradas...")
                    try:
                        # ORS aceita até 50 pontos. Geralmente teremos menos que 15 paradas.
                        refined_route = client.directions(
                            coordinates=refinement_coords,
                            profile="driving-car",
                            format="geojson"
                        )
                        final_geo = refined_route["features"][0]["geometry"]["coordinates"]
                    except Exception as e:
                        st.warning(f"⚠️ Não foi possível refinar o traçado exato: {e}")

                st.session_state["trip_result"] = {
                    "origin": all_pts_labels[0], "dest": all_pts_labels[-1],
                    "total_km": total_km, "cost": (total_km / kml) * price,
                    "timeline": segment_table, "map_geo": final_geo, "markers": markers,
                    "arrival": start_simulation_dt + timedelta(hours=accumulated_hours),
                    "all_pts_coords": all_pts_coords
                }

                if st.session_state.get("session"): refresh_session_cookie(st.session_state["session"])
                status.update(label="✅ Rota calculada!", state="complete", expanded=False)

        except Exception as e:
            st.error(f"❌ Erro: {e}")

    # ----- EXIBIÇÃO DO RESULTADO -----
    if st.session_state["trip_result"]:
        res = st.session_state["trip_result"]
        st.success(f"✅ Chegada: **{res['arrival'].strftime('%d/%m às %H:%M')}** | 💰 Custo: **R$ {res['cost']:.2f}**")

        all_pts = res["all_pts_coords"]
        m_res = folium.Map(location=[all_pts[0][1], all_pts[0][0]], zoom_start=6)
        folium.PolyLine([[p[1], p[0]] for p in res["map_geo"]], color="#2962FF", weight=5).add_to(m_res)
        
        folium.Marker(all_pts[0][::-1], tooltip="Origem", icon=folium.Icon(color="green")).add_to(m_res)
        folium.Marker(all_pts[-1][::-1], tooltip="Destino", icon=folium.Icon(color="black", icon="flag")).add_to(m_res)
        for i, pt in enumerate(all_pts[1:-1]):
            folium.Marker(pt[::-1], tooltip=f"Waypoint {i+1}", icon=folium.Icon(color="blue")).add_to(m_res)
            
        for mk in res["markers"]:
            folium.Marker(mk["loc"], popup=folium.Popup(mk["popup"], max_width=280), icon=folium.Icon(color=mk["color"], icon="info-sign")).add_to(m_res)

        st_folium(m_res, use_container_width=True, height=520, returned_objects=[])
        st.subheader("📋 Roteiro Detalhado")
        st.dataframe(pd.DataFrame(res["timeline"]), use_container_width=True, hide_index=True)

        if st.button("💾 Salvar na Comunidade"):
            ok, msg = utils.save_trip(st.session_state["user"].id, res["origin"], res["dest"], res["total_km"], price, res["cost"], res["timeline"])
            if ok: st.toast("Viagem salva!", icon="🎉")
            else: st.error(msg)


# ==============================================================================
# ABA 2: COMUNIDADE
# ==============================================================================
with tab_community:
    st.header("🌍 Comunidade")
    trips = utils.get_community_trips()
    if trips:
        for t in trips:
            with st.expander(f"🚗 {t['origem']} ➝ {t['destino']} ({float(t['distancia_km']):.0f} km)"):
                c_a, c_b = st.columns(2)
                c_a.metric("Custo Estimado", f"R$ {float(t['custo_estimado']):.2f}")
                c_b.write(f"Combustível: R$ {float(t['preco_combustivel']):.2f}/L")
                if t.get("roteiro_json"):
                    st.dataframe(pd.DataFrame(t["roteiro_json"]), use_container_width=True, hide_index=True)
    else:
        st.info("Nenhuma viagem compartilhada ainda. Seja o primeiro! 🚀")
