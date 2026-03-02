import streamlit as st
import os
import math
import googlemaps
import openrouteservice
from supabase import create_client
import json


# --- GERENCIAMENTO DE SEGREDOS ---
def get_secret(key_name):
    if key_name in st.session_state:
        return st.session_state[key_name]
    if key_name in st.secrets:
        return st.secrets[key_name]
    elif key_name in os.environ:
        return os.environ[key_name]
    return None


ORS_KEY = get_secret("ORS_KEY")
GOOGLE_KEY = get_secret("GOOGLE_KEY")
SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_KEY = get_secret("SUPABASE_KEY")


# --- CONEXÃO DB ---
@st.cache_resource
def init_supabase():
    """Cria e reutiliza a conexão com o Supabase."""
    try:
        if SUPABASE_URL and SUPABASE_KEY:
            return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        return None


supabase = init_supabase()


# --- AUTENTICAÇÃO ---
def login_user(email, password):
    try:
        response = supabase.auth.sign_in_with_password({"email": email, "password": password})
        return response.session, None
    except Exception as e:
        return None, str(e)


def signup_user(email, password):
    try:
        response = supabase.auth.sign_up({"email": email, "password": password})
        if response.session:
            return response.session, None
        return None, "Verifique seu email para confirmar o cadastro."
    except Exception as e:
        return None, str(e)


def logout_user():
    try:
        supabase.auth.sign_out()
    except Exception:
        pass


def get_google_auth_data():
    """Gera a URL de auth e o code_verifier (PKCE)."""
    try:
        redirect_url = "http://localhost:8501"
        data = supabase.auth.sign_in_with_oauth({
            "provider": "google",
            "options": {
                "redirect_to": redirect_url,
                "skip_browser_redirect": True,
            },
        })
        # O data contém a URL e o code_verifier gerado internamente pelo SDK gotrue-py
        return {
            "url": data.url,
            "code_verifier": getattr(data, 'code_verifier', None)
        }
    except Exception as e:
        print(f"Erro get_google_auth_data: {e}")
        return None


def exchange_code_for_session(auth_code, code_verifier=None):
    """Troca o código pelo token, enviando o verifier se for PKCE."""
    try:
        params = {"auth_code": auth_code}
        if code_verifier:
            params["code_verifier"] = code_verifier
        res = supabase.auth.exchange_code_for_session(params)
        return res.session, None
    except Exception as e:
        return None, str(e)


def restore_session(access_token, refresh_token):
    try:
        res = supabase.auth.set_session(access_token, refresh_token)
        if res.session:
            return res.session, None
        return None, "Sessão inválida"
    except Exception as e:
        return None, str(e)


# --- BANCO DE DADOS ---
def save_trip(user_id, origin, dest, dist_km, fuel_price, total_cost, timeline):
    try:
        data = {
            "user_id": str(user_id),
            "origem": str(origin),
            "destino": str(dest),
            "distancia_km": float(dist_km),
            "preco_combustivel": float(fuel_price),
            "custo_estimado": float(total_cost),
            "roteiro_json": json.loads(json.dumps(timeline, default=str)),
            "publico": True,
        }
        supabase.table("viagens").insert(data).execute()
        return True, "Viagem salva com sucesso!"
    except Exception as e:
        return False, str(e)


@st.cache_data(ttl=120)
def get_community_trips():
    """Busca viagens públicas da comunidade com cache de 2 minutos."""
    try:
        response = (
            supabase.table("viagens")
            .select("*")
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )
        return response.data
    except Exception:
        return []


# --- MATH & APIS ---
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


@st.cache_data(ttl=300, show_spinner=False)
def search_location_options(query: str) -> list:
    """
    Busca opções de local (Cidade, Endereço ou Coordenadas).
    Aceita: "lat, lon", "Cidade, Estado", "Rua, Número, Cidade".
    """
    if not query or len(query.strip()) < 3:
        return []

    query = query.strip()

    # 1. Tenta tratar como coordenadas diretas "lat, lon"
    try:
        if "," in query:
            parts = [p.strip() for p in query.split(",")]
            if len(parts) == 2:
                lat = float(parts[0])
                lon = float(parts[1])
                # Validação básica de limites
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    return [(f"📍 Coordenadas: {lat:.4f}, {lon:.4f}", [lon, lat])]
    except ValueError:
        pass

    # 2. Busca via OpenRouteService Pelias (Cidades/Regiões)
    options = []
    try:
        client = openrouteservice.Client(key=ORS_KEY)
        # Pelias search é bom para cidades e locais geográficos
        results = client.pelias_search(text=query, size=5)
        if results and "features" in results:
            for feat in results["features"]:
                props = feat["properties"]
                label = f"🏙️ {props.get('name')}, {props.get('region', '')} ({props.get('country_a', '')})"
                coords = feat["geometry"]["coordinates"] # [lon, lat]
                options.append((label, coords))
    except Exception:
        pass

    # 3. Busca via Google Geocoding (Endereços exatos) se tiver chave
    if GOOGLE_KEY and len(options) < 5:
        try:
            gmaps = googlemaps.Client(key=GOOGLE_KEY)
            geocode_result = gmaps.geocode(query, language='pt-BR')
            for res in geocode_result:
                label = f"🏠 {res['formatted_address']}"
                loc = res['geometry']['location']
                coords = [loc['lng'], loc['lat']]
                # Evita duplicatas simples
                if not any(abs(o[1][0] - coords[0]) < 0.001 and abs(o[1][1] - coords[1]) < 0.001 for o in options):
                    options.append((label, coords))
        except Exception:
            pass

    return options[:5]


@st.cache_data(ttl=300, show_spinner=False)
def search_city_options(query: str) -> list:
    """Apenas alias para manter compatibilidade retroativa se necessário."""
    return search_location_options(query)


def find_best_place_google(lat, lon, category, ors_client_fallback):
    """
    Busca o melhor local (hotel, posto, restaurante) próximo às coordenadas dadas.
    Utiliza Google Places com fallback para geocodificação reversa ORS.
    """
    if not GOOGLE_KEY:
        return [lat, lon], "⚠️ Sem Google Key", "gray", "info", "N/A", "-"

    gmaps = googlemaps.Client(key=GOOGLE_KEY)

    config = {
        "night_stop": {"kw": "hotel pousada", "type": "lodging", "icon": "🌙🏨", "color": "darkpurple"},
        "combo": {"kw": "posto gasolina hotel", "type": "gas_station", "icon": "🏨⛽", "color": "darkblue"},
        "sleep": {"kw": "hotel pousada", "type": "lodging", "icon": "🏨", "color": "purple"},
        "fuel": {"kw": "posto gasolina", "type": "gas_station", "icon": "⛽", "color": "red"},
        "coffee": {"kw": "restaurante lanchonete", "type": "restaurant", "icon": "☕", "color": "orange"},
    }

    conf = config.get(category, config["coffee"])

    try:
        places_result = gmaps.places_nearby(
            location=(lat, lon),
            radius=50000,
            keyword=conf["kw"],
            type=conf["type"],
            rank_by="prominence",
        )
        candidates = []
        for place in places_result.get("results", []):
            rating = place.get("rating", 0)
            if rating and rating < 3.5:
                continue
            p_lat = place["geometry"]["location"]["lat"]
            p_lng = place["geometry"]["location"]["lng"]
            dist = calculate_distance(lat, lon, p_lat, p_lng)
            candidates.append({"data": place, "dist": dist})

        if candidates:
            candidates.sort(key=lambda x: x["dist"])
            best = candidates[0]["data"]
            name = best.get("name")
            rating = best.get("rating", "N/A")
            usr_rt = best.get("user_ratings_total", 0)
            vicinity = best.get("vicinity", "Endereço não informado")
            b_lat = best["geometry"]["location"]["lat"]
            b_lng = best["geometry"]["location"]["lng"]
            waze_url = f"https://waze.com/ul?ll={b_lat},{b_lng}&navigate=yes"

            popup_html = f"""
            <div style="font-family:sans-serif; min-width:200px">
                <h4 style="margin:0">{conf['icon']} {name}</h4>
                <p style="margin:2px 0; font-size:13px">⭐ {rating} ({usr_rt} avaliações)</p>
                <p style="font-size:11px; color:#555">📍 {vicinity}</p>
                <a href="{waze_url}" target="_blank"
                   style="background:#33ccff; color:white; padding:4px 8px;
                          text-decoration:none; border-radius:4px; font-size:12px;">
                    🚗 Abrir no Waze
                </a>
            </div>
            """
            return (
                [b_lat, b_lng],
                popup_html,
                conf["color"],
                conf["icon"],
                f"{name} (⭐{rating})",
                vicinity,
            )

    except googlemaps.exceptions.ApiError as e:
        print(f"Erro Google Places API: {e}")
    except Exception as e:
        print(f"Erro inesperado Google Places: {e}")

    # --- FALLBACK: geocodificação reversa ORS ---
    try:
        reverse = ors_client_fallback.pelias_reverse(point=[lon, lat], size=1)
        if reverse and len(reverse["features"]) > 0:
            props = reverse["features"][0]["properties"]
            label = props.get("label", "Local Desconhecido")
            popup_html = f"<div><b>📍 Ref Local</b><br>{label}</div>"
            return [lat, lon], popup_html, "gray", "question", f"📍 {label}", label
    except Exception:
        pass

    return [lat, lon], "Sem dados de localização", "gray", "question", "⚠️ Isolado", "-"