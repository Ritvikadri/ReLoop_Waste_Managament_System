"""
ReLoop: Smart Circular Marketplace (Team Sutra, Ideathon 2026)
Run locally:  streamlit run app.py
"""
import base64
import hashlib
import html
import io
import json
import math
import os
import time
import wave
from urllib.parse import quote, urlencode
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="ReLoop", page_icon="♻️", layout="wide")
# ----------------------------------------------------------------------------
# Supabase authentication gate: show login/signup before the ReLoop app.
# Keep one client per Streamlit browser session so users do not share auth state.
# ----------------------------------------------------------------------------
from supabase import create_client


def get_supabase_client():
    if "supabase_client" not in st.session_state:
        url = st.secrets.get("SUPABASE_URL", "")
        key = st.secrets.get("SUPABASE_KEY", st.secrets.get("SUPABASE_PUBLISHABLE_KEY", ""))
        if not url or not key:
            st.error("Supabase is not configured. Add SUPABASE_URL and SUPABASE_KEY to Streamlit secrets.")
            st.stop()
        st.session_state.supabase_client = create_client(url, key)
    return st.session_state.supabase_client


def render_brand(size=72, show_account=False):
    logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
    with open(logo_path, "rb") as logo_file:
        logo_data = base64.b64encode(logo_file.read()).decode("ascii")

    if show_account:
        brand_col, account_col = st.columns([7, 1.4], vertical_alignment="center")
    else:
        brand_col = st.container()
        account_col = None

    with brand_col:
        st.markdown(
            f"""<div style="display:flex;align-items:center;gap:8px;margin:0 0 1rem 0">
            <img src="data:image/png;base64,{logo_data}" style="width:{size}px;height:auto;display:block">
            <span style="font-size:2.5rem;font-weight:750;line-height:1;color:#F4F7F5">ReLoop</span>
            </div>""",
            unsafe_allow_html=True,
        )

    if show_account and account_col is not None:
        client = get_supabase_client()
        token = st.session_state.get("auth_token")
        if token:
            client.postgrest.auth(token)
        user_id = st.session_state.auth_user["id"]
        email_now = st.session_state.auth_user["email"]
        metadata = st.session_state.auth_user.get("metadata", {}) or {}
        profile_error = False
        try:
            profile_rows = client.table("user_profiles").select("full_name, email, phone").eq(
                "user_id", user_id
            ).limit(1).execute().data or []
        except Exception:
            profile_rows = []
            profile_error = True
        profile = profile_rows[0] if profile_rows else {}
        default_name = profile.get("full_name") or metadata.get("full_name") or email_now.split("@")[0]
        default_phone = profile.get("phone") or st.session_state.auth_user.get("phone") or metadata.get("phone") or ""

        with account_col:
            with st.popover("👤 Account", use_container_width=True):
                st.markdown("### Your account")
                st.caption("Edit the details shown on your ReLoop account and book listings.")
                if profile_error:
                    st.warning("Run the latest Supabase account migration to enable profile editing.")
                with st.form("account_profile_form"):
                    new_name = st.text_input("Name", value=default_name, key="account_name")
                    new_email = st.text_input("Email", value=email_now, key="account_email")
                    new_phone = st.text_input("Phone number", value=default_phone, key="account_phone")
                    save_profile = st.form_submit_button("Save changes", use_container_width=True)
                if save_profile:
                    if not new_name.strip():
                        st.error("Enter your name.")
                    elif "@" not in new_email or "." not in new_email.rsplit("@", 1)[-1]:
                        st.error("Enter a valid email address.")
                    elif sum(ch.isdigit() for ch in new_phone) < 7:
                        st.error("Enter a valid phone number.")
                    else:
                        try:
                            attrs = {"data": {"full_name": new_name.strip(), "phone": new_phone.strip()}}
                            email_changed = new_email.strip().lower() != email_now.lower()
                            if email_changed:
                                attrs["email"] = new_email.strip()
                            client.auth.update_user(attrs)
                            client.table("user_profiles").upsert({
                                "user_id": user_id,
                                "email": email_now if email_changed else new_email.strip(),
                                "full_name": new_name.strip(),
                                "phone": new_phone.strip(),
                            }, on_conflict="user_id").execute()
                            st.session_state.auth_user["name"] = new_name.strip()
                            st.session_state.auth_user["phone"] = new_phone.strip()
                            if email_changed:
                                st.info("Confirm the email change using the link sent by Supabase. Your current email remains active until confirmation.")
                            else:
                                st.success("Account details updated.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Could not update your account. ({type(e).__name__})")


def show_auth_gate():
    client = get_supabase_client()
    user = st.session_state.get("auth_user")

    if user:
        st.sidebar.caption(f"Signed in: {user['email']}")
        if st.sidebar.button("Log out", key="auth_logout"):
            try:
                client.auth.sign_out()
            finally:
                for key in ("auth_user", "auth_token", "supabase_client"):
                    st.session_state.pop(key, None)
                st.rerun()
        # Attach this user's JWT to database API calls so RLS sees the user.
        token = st.session_state.get("auth_token")
        if token:
            client.postgrest.auth(token)
        return user

    render_brand(88)
    st.subheader("Welcome — log in or create an account")
    login_tab, signup_tab = st.tabs(["Log in", "Sign up"])

    with login_tab:
        with st.form("reloop_login_form"):
            email = st.text_input("Email", key="reloop_login_email")
            password = st.text_input("Password", type="password", key="reloop_login_password")
            submitted = st.form_submit_button("Log in", use_container_width=True)
        if submitted:
            try:
                result = client.auth.sign_in_with_password({"email": email.strip(), "password": password})
                metadata = getattr(result.user, "user_metadata", None) or {}
                st.session_state.auth_user = {
                    "id": str(result.user.id), "email": result.user.email,
                    "phone": metadata.get("phone"), "metadata": metadata,
                    "name": metadata.get("full_name"),
                }
                st.session_state.auth_token = result.session.access_token
                st.rerun()
            except Exception:
                st.error("Login failed. Check your email and password, then try again.")

    with signup_tab:
        with st.form("reloop_signup_form"):
            full_name = st.text_input("Name", key="reloop_signup_name")
            email = st.text_input("Email", key="reloop_signup_email")
            phone = st.text_input("Phone number", placeholder="+91 98765 43210", key="reloop_signup_phone")
            password = st.text_input("Password (at least 8 characters)", type="password", key="reloop_signup_password")
            confirm = st.text_input("Confirm password", type="password", key="reloop_signup_confirm")
            submitted = st.form_submit_button("Create account", use_container_width=True)
        if submitted:
            if password != confirm:
                st.error("The passwords do not match.")
            elif len(password) < 8:
                st.error("Use a password with at least 8 characters.")
            elif not full_name.strip():
                st.error("Enter your name.")
            elif sum(ch.isdigit() for ch in phone) < 7:
                st.error("Enter a valid phone number.")
            else:
                try:
                    result = client.auth.sign_up({
                        "email": email.strip(),
                        "password": password,
                        "options": {"data": {"full_name": full_name.strip(), "phone": phone.strip()}},
                    })
                    if result.session:
                        st.session_state.auth_user = {
                            "id": str(result.user.id), "email": result.user.email,
                            "phone": phone.strip(), "name": full_name.strip(),
                            "metadata": {"full_name": full_name.strip(), "phone": phone.strip()},
                        }
                        st.session_state.auth_token = result.session.access_token
                        st.rerun()
                    else:
                        st.success("Account created. Confirm your email, then return here and log in.")
                except Exception as e:
                    detail = str(e)
                    lowered = detail.lower()
                    if "already registered" in lowered or "already exists" in lowered:
                        st.error("This email already has an account. Choose Log in, or use password reset.")
                    else:
                        st.error(f"Signup failed ({type(e).__name__}): {detail[:260]}")

    st.stop()


current_user = show_auth_gate()
render_brand(64, show_account=True)

# ----------------------------------------------------------------------------
# Design tokens
# ----------------------------------------------------------------------------
SPRUCE = "#12332F"      # dark text on marigold
BG = "#0A1514"
SURFACE = "#111F1D"
SURFACE2 = "#172A27"
LINE = "#25413D"
TEXT = "#E9F0ED"
MUTE = "#8FA8A2"
MARIGOLD = "#F2B138"
MINT = "#6FD3B0"
CORAL = "#EE8264"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700;12..96,800&family=DM+Sans:wght@400;500;700&display=swap');

html, body, [class*="css"], .stApp {{ font-family: 'DM Sans', sans-serif; color: {TEXT}; }}
.stApp {{
  background:
    radial-gradient(1100px 520px at 95% -8%, rgba(242,177,56,.10), transparent 60%),
    radial-gradient(900px 500px at -5% 105%, rgba(111,211,176,.09), transparent 60%),
    {BG};
}}
h1, h2, h3, .rl-display {{ font-family: 'Bricolage Grotesque', sans-serif !important; letter-spacing: -0.02em; color: {TEXT}; }}
h3 {{ font-size: 1.7rem !important; }}
header[data-testid="stHeader"], footer, #MainMenu {{ visibility: hidden; height: 0; }}
.block-container {{ padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1200px; }}
::selection {{ background: {MARIGOLD}; color: {SPRUCE}; }}
[data-testid="stElementContainer"]:has(iframe[height="0"]), .element-container:has(iframe[height="0"]) {{ height: 0; margin: 0; overflow: hidden; position: absolute; }}

/* ---------- hero ---------- */
.rl-hero {{
  position: relative; overflow: hidden;
  background:
    radial-gradient(700px 360px at 88% 0%, rgba(242,177,56,.20), transparent 62%),
    radial-gradient(600px 360px at 0% 100%, rgba(111,211,176,.14), transparent 62%),
    linear-gradient(160deg, #132624 0%, #0D1B19 100%);
  border: 1px solid {LINE}; border-radius: 30px;
  padding: 52px 54px; display: grid; grid-template-columns: 1.2fr 1fr; gap: 40px; align-items: center;
  box-shadow: 0 30px 80px -30px rgba(0,0,0,.7), inset 0 1px 0 rgba(255,255,255,.05);
}}
.rl-hero h1 {{ font-size: 3.7rem; line-height: .98; margin: 18px 0 16px 0; font-weight: 800; color: {TEXT}; }}
.rl-hero p {{ font-size: 1.12rem; color: #B7C9C4; max-width: 36ch; margin: 0; line-height: 1.55; }}
.rl-pill {{ display:inline-block; background: rgba(242,177,56,.12); color: {MARIGOLD}; border: 1px solid rgba(242,177,56,.35);
  padding: 6px 14px; border-radius: 999px; font-size: .85rem; font-weight: 500; }}
.rl-chips {{ margin-top: 24px; display:flex; gap:10px; flex-wrap: wrap; }}
.rl-chip {{ background: rgba(255,255,255,.05); border: 1px solid {LINE}; border-radius: 12px; padding: 8px 14px; font-size: .92rem; color: {TEXT}; }}
.rl-chip b {{ color: {MARIGOLD}; font-weight: 700; }}

/* Rate board: yellow glow brightens and spreads as the pointer approaches. */
.rl-board {{ --prox: 0; position: relative; background: #07110F; border-radius: 20px; padding: 22px 24px;
  border: none;
  box-shadow:
    0 0 calc(18px + var(--prox) * 68px) rgba(242, 177, 56, calc(.12 + var(--prox) * .62)),
    0 12px calc(36px + var(--prox) * 110px) rgba(242, 177, 56, calc(.10 + var(--prox) * .44));
  transition: box-shadow .18s ease; }}
.rl-board h4 {{ margin: 0 0 12px 0; filter: brightness(calc(1 + var(--prox) * .45)); color: {MARIGOLD}; font-family: 'Bricolage Grotesque'; font-size: 1.1rem; font-weight: 700; display:flex; align-items:center; gap:10px; }}
.rl-dot {{ width:9px; height:9px; border-radius:50%; background:{MINT}; box-shadow: 0 0 10px {MINT}; }}
.rl-row {{ display:flex; justify-content: space-between; padding: 9px 0; border-bottom: 1px dashed #2A4A45; color: #DCE8E4; font-size: 1rem; }}
.rl-row:last-child {{ border-bottom: 0; }}
.rl-row b {{ filter: brightness(calc(1 + var(--prox) * .45)); color: {MARIGOLD}; font-weight: 700; font-variant-numeric: tabular-nums; }}
/* ticker */
.rl-ticker {{ overflow: hidden; margin: 18px 0 8px 0; border-top: 1px solid {LINE}; border-bottom: 1px solid {LINE}; padding: 11px 0; }}
.rl-track {{ display:inline-flex; gap: 46px; white-space: nowrap; animation: rl-scroll 46s linear infinite; color: {MUTE}; font-size: .95rem; }}
.rl-track b {{ color: {MARIGOLD}; margin-left: 6px; font-weight: 700; }}
@keyframes rl-scroll {{ from {{ transform: translateX(0); }} to {{ transform: translateX(-50%); }} }}
@media (prefers-reduced-motion: reduce) {{ .rl-track {{ animation: none; }} }}

/* flow */
.rl-flow {{ display:grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin: 14px 0 26px 0; }}
.rl-flow div {{ background: {SURFACE}; border: 1px solid {LINE}; border-left: 3px solid {MARIGOLD}; border-radius: 14px; padding: 14px 18px; }}
.rl-flow b {{ display:block; font-family:'Bricolage Grotesque'; font-size: 1.08rem; color: {TEXT}; }}
.rl-flow span {{ color: {MUTE}; font-size: .9rem; }}

/* ---------- cards ---------- */
.rl-card {{ background: linear-gradient(180deg, {SURFACE2} 0%, {SURFACE} 100%); border: 1px solid {LINE}; border-radius: 20px;
  padding: 20px 22px; height: 100%; box-shadow: 0 18px 40px -26px rgba(0,0,0,.8), inset 0 1px 0 rgba(255,255,255,.04);
  transition: transform .18s ease, border-color .18s ease; }}
.rl-card:hover {{ transform: translateY(-3px); border-color: #3B6A63; }}
.rl-card h4 {{ margin: 0 0 4px 0; font-family:'Bricolage Grotesque'; font-size: 1.12rem; color: {TEXT}; line-height: 1.25; }}
.rl-meta {{ color: {MUTE}; font-size: .9rem; }}
.rl-price {{ font-family:'Bricolage Grotesque'; font-size: 1.7rem; font-weight: 800; color: {MARIGOLD}; font-variant-numeric: tabular-nums; }}
.rl-strike {{ color: {MUTE}; text-decoration: line-through; font-size: .92rem; margin-left: 8px; }}
.rl-tag {{ display:inline-block; background: rgba(111,211,176,.10); color: {MINT}; border: 1px solid rgba(111,211,176,.25);
  border-radius: 9px; padding: 2px 10px; font-size:.8rem; margin: 8px 6px 0 0; }}
.rl-tag.best {{ background: rgba(242,177,56,.14); color: {MARIGOLD}; border-color: rgba(242,177,56,.4); }}
.rl-bar {{ height: 6px; border-radius: 99px; background: #0A1514; margin-top: 12px; overflow: hidden; }}
.rl-bar i {{ display:block; height: 100%; border-radius: 99px; background: linear-gradient(90deg, {MINT}, {MARIGOLD}); }}

.rl-value {{ background: linear-gradient(135deg, #F7C25A, #E9A021); color: {SPRUCE}; border-radius: 22px; padding: 24px 28px;
  box-shadow: 0 24px 60px -24px rgba(242,177,56,.55); }}
.rl-value .n {{ font-family:'Bricolage Grotesque'; font-size: 2.7rem; font-weight: 800; line-height:1.05; margin-top: 4px; }}

.rl-verdict {{ border-radius: 24px; padding: 28px 32px; color: #fff; border: 1px solid rgba(255,255,255,.14);
  box-shadow: 0 30px 70px -28px var(--glow, rgba(0,0,0,.6)); }}
.rl-verdict .v {{ font-family:'Bricolage Grotesque'; font-size: 3rem; font-weight: 800; line-height: 1; margin-top: 6px; }}
.rl-verdict p {{ margin: 10px 0 0 0; opacity: .92; max-width: 40ch; }}

/* ---------- workspace label row (sits directly under the logo) ---------- */
.rl-workspace-bar {{ display:flex; justify-content: space-between; align-items: center; gap: 16px; margin: 0 0 14px 0; padding: 0 4px; }}
.rl-eyebrow {{ color: {MINT}; font-size: .82rem; font-weight: 800; letter-spacing: .22em; text-transform: uppercase; }}
.rl-hint {{ color: {MUTE}; font-size: 1rem; }}
@media (max-width: 640px) {{
  .rl-workspace-bar {{ flex-direction: column; align-items: flex-start; gap: 4px; }}
}}

/* ---------- primary section navigation ---------- */
.stTabs {{ margin: 0 0 30px; }}
.stTabs [data-baseweb="tab-list"] {{
  display: flex; justify-content: center; align-items: center; gap: 12px;
  border: 1px solid rgba(111,211,176,.13); border-radius: 18px;
  background: linear-gradient(110deg, rgba(17,31,29,.94), rgba(13,25,23,.88));
  box-shadow: 0 14px 36px -28px rgba(0,0,0,.85), inset 0 1px 0 rgba(255,255,255,.035);
  padding: 10px; overflow-x: auto;
}}
.stTabs [data-baseweb="tab"] {{
  flex: 1 1 0; min-width: max-content; justify-content: center;
  background: transparent !important; border: 1px solid transparent !important;
  border-radius: 12px; padding: 12px 20px; height: auto;
  color: {MUTE} !important; font-weight: 600; letter-spacing: .01em;
  transition: color .18s ease, border-color .18s ease, background .18s ease, transform .18s ease;
}}
.stTabs [data-baseweb="tab"]:hover {{
  color: {TEXT} !important; background: rgba(255,255,255,.035) !important;
  border-color: rgba(255,255,255,.18) !important; transform: translateY(-1px);
}}
.stTabs [data-baseweb="tab"][aria-selected="true"] {{
  color: #FFFFFF !important; background: rgba(255,255,255,.045) !important;
  border: 1px solid rgba(255,255,255,.88) !important;
  box-shadow: 0 0 0 1px rgba(255,255,255,.08), 0 5px 18px -12px rgba(255,255,255,.32);
  font-weight: 700;
}}
.stTabs [data-baseweb="tab"] p {{ color: inherit !important; font-weight: inherit; }}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{ display:none !important; }}
@media (max-width: 640px) {{
  .stTabs [data-baseweb="tab-list"] {{ justify-content: flex-start; gap: 6px; padding: 7px; }}
  .stTabs [data-baseweb="tab"] {{ flex: 0 0 auto; padding: 10px 14px; }}
}}

.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
  border-radius: 12px; border: 0; background: {MARIGOLD}; color: {SPRUCE}; font-weight: 700; padding: 9px 20px;
  box-shadow: 0 10px 24px -12px rgba(242,177,56,.7); transition: transform .12s ease, filter .12s ease; }}
.stButton > button:hover, .stFormSubmitButton > button:hover {{ filter: brightness(1.08); transform: translateY(-1px); color: {SPRUCE}; border: 0; }}
.stButton > button:focus-visible {{ outline: 3px solid {MINT}; outline-offset: 2px; }}
.stButton > button:disabled {{ background: {SURFACE2}; color: {MUTE}; box-shadow: none; }}
.st-key-list_book_button button {{
  --list-prox: 0;
  background: linear-gradient(135deg, #FFD45C, #F2B138);
  font-weight: 800; border: 0;
  box-shadow: 0 0 calc(10px + var(--list-prox) * 30px) rgba(242,177,56,calc(.28 + var(--list-prox) * .5)),
              0 8px calc(22px + var(--list-prox) * 38px) rgba(242,177,56,calc(.18 + var(--list-prox) * .35));
  transition: box-shadow .15s ease, transform .15s ease, filter .15s ease;
}}
.st-key-list_book_button button:hover {{ filter: brightness(1.12); transform: translateY(-2px); }}


[data-testid="stMetric"] {{ background: linear-gradient(180deg, {SURFACE2}, {SURFACE}); border: 1px solid {LINE}; border-radius: 18px; padding: 16px 20px; }}
[data-testid="stMetricValue"] {{ font-family: 'Bricolage Grotesque'; font-weight: 800; color: {MARIGOLD}; }}
[data-testid="stMetricLabel"] p {{ color: {MUTE}; }}

[data-baseweb="select"] > div, .stTextInput input, .stNumberInput input {{ background: {SURFACE} !important; border-color: {LINE} !important; border-radius: 12px !important; }}
[data-testid="stFileUploaderDropzone"] {{ background: {SURFACE}; border: 1.5px dashed #3B6A63; border-radius: 16px; }}
[data-testid="stExpander"] {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 16px; }}
[data-testid="stForm"] {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 16px; }}
[data-testid="stDataFrame"], [data-testid="stImage"] img {{ border-radius: 16px; }}
[data-testid="stCaptionContainer"] {{ color: {MUTE}; }}

@media (max-width: 900px) {{
  .rl-hero {{ grid-template-columns: 1fr; padding: 30px 24px; }}
  .rl-hero h1 {{ font-size: 2.4rem; }}
  .rl-flow {{ grid-template-columns: 1fr 1fr; }}
}}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# Main navigation: sits directly under the logo, above everything else.
# The tab containers are created here; their content is filled in further down.
# ----------------------------------------------------------------------------
st.markdown(
    '<div class="rl-workspace-bar"><span class="rl-eyebrow">Your ReLoop workspace</span>'
    '<span class="rl-hint"></span></div>',
    unsafe_allow_html=True,
)
tab_scrap, tab_books, tab_sound, tab_impact = st.tabs(
    ["Scrap Vision", "Book Loop", "Sound Check", "Impact"]
)

# ----------------------------------------------------------------------------
# Data (sample data for the prototype; replace with Supabase tables later)
# ----------------------------------------------------------------------------
RATES = {  # illustrative Rs per kg, localize city by city
    "Newspaper / paper": 12, "Cardboard": 8, "Plastic (mixed)": 15, "Iron": 28,
    "Steel": 35, "Aluminium": 105, "Brass": 400, "Copper": 650,
    "Glass": 2, "E-waste (mixed)": 40,
}

AREAS = {
    "Kumaraswamy Layout": (12.9082, 77.5646),
    "Banashankari": (12.9255, 77.5468),
    "JP Nagar": (12.9063, 77.5857),
    "Jayanagar": (12.9308, 77.5838),
}

DEALERS = [  # name, lat, lon, share of fair rate offered, speciality
    ("Ramesh Traders", 12.9140, 77.5600, 0.88, "Paper, plastic"),
    ("Shree Metals", 12.9230, 77.5530, 0.95, "Metals, e-waste"),
    ("Lakshmi Scrap Centre", 12.9010, 77.5900, 0.82, "All household scrap"),
    ("Basavanna Recyclers", 12.9300, 77.5800, 0.91, "Paper, cardboard, glass"),
]

SEED_BOOKS = [
    ("HC Verma: Concepts of Physics, Vol 1", "JEE", "Physics", "Good", 495, 200, "Sadhvi"),
    ("HC Verma: Concepts of Physics, Vol 2", "JEE", "Physics", "Good", 475, 190, "Sadhvi"),
    ("Cengage Organic Chemistry", "JEE", "Chemistry", "Like new", 780, 340, "Ritvik"),
    ("Cengage Calculus", "JEE", "Maths", "Fair", 850, 320, "Sanvi"),
    ("NCERT Biology, Class 12", "NEET", "Biology", "Like new", 190, 90, "Sanvi"),
    ("Trueman's Objective Biology", "NEET", "Biology", "Good", 720, 300, "Ritvik"),
    ("RD Sharma, Class 11", "Boards", "Maths", "Good", 650, 260, "Sadhvi"),
    ("Lakhmir Singh Chemistry, Class 10", "Boards", "Chemistry", "Fair", 420, 150, "Sanvi"),
]


def init_state():
    ss = st.session_state
    if "books" not in ss:
        ss.books = [
            dict(id=i, title=t, exam=e, subject=s, cond=c, mrp=m, price=p, seller=n, requested=False)
            for i, (t, e, s, c, m, p, n) in enumerate(SEED_BOOKS)
        ]
    ss.setdefault("kg_valued", 0.0)
    ss.setdefault("rupees_valued", 0.0)
    ss.setdefault("books_passed", 0)
    ss.setdefault("rupees_saved", 0)
    ss.setdefault("verdicts", {"RESELL": 0, "REPAIR": 0, "SCRAP": 0})
    ss.setdefault("scrap_df", None)
    ss.setdefault("scrap_ver", 0)


init_state()


def db_client():
    client = get_supabase_client()
    token = st.session_state.get("auth_token")
    if token:
        client.postgrest.auth(token)
    return client


def load_book_listings():
    return (db_client().table("book_listings").select("*")
            .eq("status", "active").order("created_at", desc=True).execute().data or [])


def load_my_book_listings():
    return (db_client().table("book_listings").select("*")
            .eq("owner_id", current_user["id"]).order("created_at", desc=True).execute().data or [])


def load_book_requests():
    return (db_client().table("book_requests").select("listing_id")
            .eq("requester_id", current_user["id"]).execute().data or [])


def load_requests_for_listings(listing_ids):
    if not listing_ids:
        return []
    return (db_client().table("book_requests").select("listing_id, requester_id, created_at")
            .in_("listing_id", listing_ids).execute().data or [])


def load_contact_profiles(user_ids):
    if not user_ids:
        return {}
    rows = (db_client().table("user_profiles").select("user_id, email, phone")
            .in_("user_id", list(user_ids)).execute().data or [])
    return {str(row["user_id"]): row for row in rows}


def add_impact_record(kind, kg=0, amount=0, details=None):
    db_client().table("impact_records").insert({
        "user_id": current_user["id"],
        "kind": kind,
        "kg": kg,
        "amount": amount,
        "details": details or {},
    }).execute()


def load_impact_records():
    return (db_client().table("impact_records").select("kind, kg, amount, details, created_at")
            .eq("user_id", current_user["id"]).execute().data or [])

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def haversine_km(a, b):
    r = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def get_api_key_with_source():
    """Returns (key, where it came from)."""
    try:
        key = st.secrets["GEMINI_API_KEY"]
        if key:
            return str(key).strip().strip('"').strip("'").strip(), "Streamlit secrets.toml"
    except Exception:
        pass
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key.strip().strip('"').strip("'").strip(), "Windows environment variable GEMINI_API_KEY"
    return "", "nowhere"


def get_api_key():
    """Reads the Gemini key from Streamlit secrets (local or cloud), then from an environment variable."""
    return get_api_key_with_source()[0]


def key_status():
    """Masked description of the key the app is actually using, to help debug."""
    k, src = get_api_key_with_source()
    if not k:
        return "No key found. The app is in demo mode."
    ok = "looks right" if (k.startswith("AIza") or k.startswith("AQ.")) and len(k) >= 35 else "does NOT look like a Google key (should start with AIza or AQ.)"
    return f"Key in use: {k[:4]}...{k[-3:]} ({len(k)} characters), {ok}. Source: {src}. Run folder: {os.getcwd()}"


def get_model():
    """Model name can be overridden with GEMINI_MODEL in secrets."""
    try:
        return st.secrets["GEMINI_MODEL"] or "gemini-3.5-flash-lite"
    except Exception:
        return os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")


def has_api_key():
    return bool(get_api_key())


def shrink_image(img_bytes, max_side=896):
    """Smaller photo = much faster upload and analysis, with no loss in accuracy for scrap piles."""
    from PIL import Image, ImageOps

    im = ImageOps.exif_transpose(Image.open(io.BytesIO(img_bytes))).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return buf.getvalue()


@lru_cache(maxsize=4)
def _client_for(key):
    from google import genai

    return genai.Client(api_key=key)


def detect_raw(img_bytes, model, key):
    """Gemini reads ONE photo and returns (table, timings). No Streamlit calls, so it is safe in threads."""
    from google.genai import types

    t0 = time.perf_counter()
    small = shrink_image(img_bytes, 768)
    t_resize = time.perf_counter() - t0

    prompt = (
        "Photo of household scrap. Give the materials visible and a rough weight in kg each. "
        "Only use these names: " + ", ".join(RATES)
        + '. Reply JSON only: {"items":[{"material":"Cardboard","kg":3.5}]}'
    )
    contents = [types.Part.from_bytes(data=small, mime_type="image/jpeg"), prompt]
    client = _client_for(key)
    base = dict(response_mime_type="application/json", max_output_tokens=512)

    t1 = time.perf_counter()
    resp = None
    if "lite" not in model:
        try:
            cfg = types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level="low"), **base)
            resp = client.models.generate_content(model=model, contents=contents, config=cfg)
        except Exception:
            resp = None
    if resp is None:
        resp = client.models.generate_content(model=model, contents=contents,
                                              config=types.GenerateContentConfig(**base))
    t_api = time.perf_counter() - t1

    text = resp.text
    data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    rows = [(i["material"], float(i["kg"])) for i in data["items"] if i["material"] in RATES]
    if not rows:
        raise ValueError("No known materials found")
    df = pd.DataFrame(rows, columns=["Material", "Weight (kg)"])
    return df, dict(model=model, resize=t_resize, api=t_api)


def analyze_photos(photos, picks):
    """Analyses every photo at the same time. Returns (results, errors). Repeat photos come from a cache."""
    key, model = get_api_key(), get_model()
    cache = st.session_state.setdefault("detect_cache", {})
    results = [None] * len(photos)
    todo = []
    for i, (name, data) in enumerate(photos):
        h = hashlib.sha1(data).hexdigest() + model
        if h in cache:
            results[i] = cache[h]
        else:
            todo.append((i, h, data))
    errors = []
    if todo:
        with ThreadPoolExecutor(max_workers=min(4, len(todo))) as ex:
            futs = [(i, h, ex.submit(detect_raw, data, model, key)) for i, h, data in todo]
            for i, h, f in futs:
                try:
                    results[i] = cache[h] = f.result()
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{photos[i][0]}: {type(e).__name__}: {str(e)[:300]}")
                    results[i] = (pick_df(picks), dict(model="quick pick", resize=0.0, api=0.0))
    return results, errors


DEFAULT_KG = {"Newspaper / paper": 6.0, "Cardboard": 4.0, "Plastic (mixed)": 3.0, "Iron": 5.0, "Steel": 4.0,
              "Aluminium": 1.5, "Brass": 0.5, "Copper": 0.5, "Glass": 5.0, "E-waste (mixed)": 2.0}


def pick_df(picks):
    picks = picks or ["Newspaper / paper"]
    return pd.DataFrame([(m, DEFAULT_KG[m]) for m in picks], columns=["Material", "Weight (kg)"])


DEMO_DETECTION = pd.DataFrame(
    [("Newspaper / paper", 6.0), ("Cardboard", 3.0), ("Iron", 2.5), ("Aluminium", 0.8)],
    columns=["Material", "Weight (kg)"],
)


def read_wav(raw: bytes):
    with wave.open(io.BytesIO(raw)) as w:
        sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        frames = w.readframes(n)
    if sw == 1:
        x = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128) / 128
    elif sw == 2:
        x = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768
    elif sw == 4:
        x = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648
    else:
        raise ValueError("Unsupported WAV bit depth")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return sr, x[: sr * 12]  # use at most 12 seconds


def analyze_audio(raw: bytes):
    """
    Prototype heuristic standing in for a fine-tuned YAMNet classifier.
    A healthy fan or mixer gives a steady, tonal hum. Faults show up as unstable loudness,
    noisy (flat) spectrum and extra high-frequency rattle.
    """
    sr, x = read_wav(raw)
    x = x - np.mean(x)
    rms = float(np.sqrt(np.mean(x ** 2)))
    if rms < 0.004:
        return dict(rms=rms, cv=0, flat=0, hf=0, fault=1.0, silent=True, x=x, sr=sr)

    hop = int(sr * 0.05)
    frames = [x[i:i + hop] for i in range(0, len(x) - hop, hop)]
    env = np.array([np.sqrt(np.mean(f ** 2)) for f in frames])
    cv = float(env.std() / (env.mean() + 1e-9))

    n_fft = 2048
    win = np.hanning(n_fft)
    spec = np.zeros(n_fft // 2 + 1)
    count = 0
    for i in range(0, len(x) - n_fft, n_fft // 2):
        spec += np.abs(np.fft.rfft(x[i:i + n_fft] * win)) ** 2
        count += 1
    spec /= max(count, 1)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    band = (freqs >= 80) & (freqs <= 8000)
    p = spec[band] + 1e-12
    flat = float(np.exp(np.mean(np.log(p))) / np.mean(p))
    hf = float(spec[(freqs >= 2000) & (freqs <= 8000)].sum() / (spec[band].sum() + 1e-12))

    fault = 0.4 * min(cv / 0.6, 1) + 0.35 * min(flat / 0.3, 1) + 0.25 * min(hf / 0.5, 1)
    return dict(rms=rms, cv=cv, flat=flat, hf=hf, fault=float(fault), silent=False, x=x, sr=sr)


def verdict_for(fault, silent):
    if silent:
        return "SCRAP", "#7A3B2E|#B5503A", "No running sound picked up. Check the recording, or treat as dead."
    if fault < 0.35:
        return "RESELL", "#0F4A38|#1E8A63", "Steady, clean sound. This looks healthy enough to sell as working."
    if fault < 0.65:
        return "REPAIR", "#6B4A0C|#C58A1B", "Some rattle or uneven speed. A small repair could restore its value."
    return "SCRAP", "#7A3B2E|#B5503A", "Rough, unstable sound. Sell it for parts or metal."


# ----------------------------------------------------------------------------
# Hero (shown at the top of the Scrap Vision tab, under the main navigation)
# ----------------------------------------------------------------------------
board_rows = "".join(
    f'<div class="rl-row"><span>{m}</span><b>₹{r}/kg</b></div>'
    for m, r in [("Newspaper / paper", 12), ("Iron", 28), ("Aluminium", 105), ("Copper", 650)]
)
ticker = "".join(f'<span>{m}<b>₹{r}/kg</b></span>' for m, r in RATES.items())
tab_scrap.markdown(
    f"""
<div class="rl-hero">
  <div>
    <span class="rl-pill">Team Sutra · Ideathon 2026</span>
    <h1 class="rl-display">Know what it's WORTH before you sell it.</h1>
    <p>Reuse it, resell it, or recycle it. ReLoop helps you decide, in that order.</p>
    <div class="rl-chips">
      <span class="rl-chip"><b>Scrap</b> priced by photo</span>
      <span class="rl-chip"><b>Books</b> passed to juniors</span>
      <span class="rl-chip"><b>Appliances</b> judged by sound</span>
    </div>
  </div>
  <div class="rl-board">
    <h4><span class="rl-dot"></span>Today's scrap rates (sample)</h4>
    {board_rows}
  </div>
</div>
<div class="rl-ticker"><div class="rl-track">{ticker}{ticker}</div></div>
<div class="rl-flow">
  <div><b>Capture</b><span>photo, book or sound</span></div>
  <div><b>Analyse</b><span>vision and audio AI</span></div>
  <div><b>Decide</b><span>value, resell or scrap</span></div>
  <div><b>Connect</b><span>kabadiwala or buyer</span></div>
</div>
""",
    unsafe_allow_html=True,
)

components.html(
    """
<script>
(function () {
  try {
    var p = window.parent;
    if (p.__rlProxHandler) p.document.removeEventListener("mousemove", p.__rlProxHandler);
    var R = 380; // distance in px at which the glow starts
    p.__rlProxHandler = function (e) {
      var board = p.document.querySelector(".rl-board");
      if (!board) return;
      var r = board.getBoundingClientRect();
      var dx = Math.max(r.left - e.clientX, 0, e.clientX - r.right);
      var dy = Math.max(r.top - e.clientY, 0, e.clientY - r.bottom);
      var t = Math.max(0, Math.min(1, 1 - Math.hypot(dx, dy) / R));
      t = t * t * (3 - 2 * t); // smoothstep
      board.style.setProperty("--prox", t.toFixed(3));
      var listButton = p.document.querySelector(".st-key-list_book_button button");
      if (listButton) {
        var b = listButton.getBoundingClientRect();
        var bx = Math.max(b.left - e.clientX, 0, e.clientX - b.right);
        var by = Math.max(b.top - e.clientY, 0, e.clientY - b.bottom);
        var bt = Math.max(0, Math.min(1, 1 - Math.hypot(bx, by) / 260));
        bt = bt * bt * (3 - 2 * bt);
        listButton.style.setProperty("--list-prox", bt.toFixed(3));
      }
    };
    p.document.addEventListener("mousemove", p.__rlProxHandler);
  } catch (err) {}
})();
</script>
""",
    height=0,
)

# ----------------------------------------------------------------------------
# Scrap Vision
# ----------------------------------------------------------------------------
with tab_scrap:
    st.subheader("What is your scrap worth?")
    left, right = st.columns([1, 1.15], gap="large")
    photos = []

    with left:
        mode = st.radio("Photo source", ["Upload photos", "Use camera"], horizontal=True, label_visibility="collapsed")
        if mode == "Upload photos":
            files = st.file_uploader("Photos of your scrap (add as many as you like)", type=["jpg", "jpeg", "png"],
                                     accept_multiple_files=True)
            photos = [(f.name, f.getvalue()) for f in (files or [])]
        else:
            shots = st.session_state.setdefault("shots", [])
            shot = st.camera_input("Take a photo of your scrap")
            c_a, c_b = st.columns(2)
            if shot is not None and c_a.button("Add this photo to the pile", key="add_shot"):
                shots.append((f"Camera photo {len(shots) + 1}", shot.getvalue()))
                st.rerun()
            if shots and c_b.button("Clear camera photos", key="clear_shots"):
                shots.clear()
                st.rerun()
            photos = list(shots)

        if photos:
            gcols = st.columns(min(3, len(photos)))
            for i, (n, data) in enumerate(photos):
                gcols[i % len(gcols)].image(data, caption=n, use_container_width=True)
            st.caption(f"{len(photos)} photo{'s' if len(photos) != 1 else ''} in the pile. Add more any time, they all stay.")
            picks = st.multiselect(
                "Quick pick: what is in the pile?",
                list(RATES),
                default=["Newspaper / paper"],
                help="Used for a photo when AI analysis is not available. You can edit the weights on the right.",
            )
            if st.button("Identify materials", key="detect"):
                st.session_state.scrap_ver += 1
                with st.spinner(f"Reading {len(photos)} photo{'s' if len(photos) != 1 else ''}..."):
                    t0 = time.perf_counter()
                    errors = []
                    if has_api_key():
                        results, errors = analyze_photos(photos, picks)
                    else:
                        results = [(pick_df(picks), dict(model="quick pick", resize=0.0, api=0.0)) for _ in photos]
                        st.info("Demo mode: using your quick pick. Add GEMINI_API_KEY to .streamlit/secrets.toml for real photo analysis.")
                    elapsed = time.perf_counter() - t0
                st.session_state.photo_results = [
                    dict(name=n, df=r[0], tm=r[1]) for (n, _), r in zip(photos, results)
                ]
                st.session_state.scrap_df = (
                    pd.concat([r[0] for r in results])
                    .groupby("Material", as_index=False, sort=False)["Weight (kg)"].sum()
                )
                st.caption(f"Analysed {len(photos)} photo{'s' if len(photos) != 1 else ''} in {elapsed:.1f}s "
                           f"with {results[0][1]['model']}. All photos are combined in the table.")
                if errors:
                    st.warning("Some photos could not be analysed, so your quick pick is used for them.")
                    st.error("Gemini error: " + " || ".join(errors)[:700])
                    st.info(key_status())
        else:
            st.caption("No photo yet? You can still enter materials by hand on the right.")

    with right:
        if st.session_state.scrap_df is None:
            st.session_state.scrap_df = pd.DataFrame([("Newspaper / paper", 5.0)], columns=["Material", "Weight (kg)"])
        st.markdown("**All photos combined. Check and edit.**")
        df = st.data_editor(
            st.session_state.scrap_df,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            column_config={
                "Material": st.column_config.SelectboxColumn(options=list(RATES), required=True),
                "Weight (kg)": st.column_config.NumberColumn(min_value=0.0, step=0.5, format="%.1f"),
            },
            key=f"scrap_editor_{st.session_state.scrap_ver}",
        )
        df = df.dropna()
        df = df[df["Material"].isin(RATES)]
        kg = float(df["Weight (kg)"].sum()) if len(df) else 0.0
        fair = float(sum(RATES[m] * w for m, w in zip(df["Material"], df["Weight (kg)"])))

        st.markdown(
            f'<div class="rl-value"><div class="rl-meta" style="color:{SPRUCE};font-weight:500">Fair value of {kg:.1f} kg, all photos</div>'
            f'<div class="n">₹{fair * 0.9:,.0f} to ₹{fair * 1.1:,.0f}</div></div>',
            unsafe_allow_html=True,
        )

        names_now = [n for n, _ in photos]
        pr = [p for p in st.session_state.get("photo_results", []) if p["name"] in names_now]
        if pr:
            st.markdown("&nbsp;")
            rows_ = []
            for p in pr:
                d = p["df"]
                k_ = float(d["Weight (kg)"].sum())
                v_ = float(sum(RATES[m] * w for m, w in zip(d["Material"], d["Weight (kg)"])))
                rows_.append({"Photo": p["name"], "kg": round(k_, 1), "Value (₹)": round(v_)})
            st.dataframe(pd.DataFrame(rows_), hide_index=True, use_container_width=True)
            a1, a2, a3 = st.columns(3)
            a1.metric("Photos", len(pr))
            a2.metric("Average price per kg", f"₹{fair / kg:,.1f}" if kg else "n/a")
            a3.metric("Average per photo", f"₹{fair / len(pr):,.0f}")
            st.caption("Per-photo rows are the AI estimates. The averages use the combined table above, including your edits.")

    st.markdown("&nbsp;")
    st.subheader("Who should you sell to?")
    area = st.selectbox("Your area", list(AREAS))
    me = AREAS[area]
    rows = []
    for name, la, lo, share, spec in DEALERS:
        rows.append(dict(name=name, km=haversine_km(me, (la, lo)), offer=fair * share, spec=spec, share=share))
    rows.sort(key=lambda r: -r["offer"])
    cols = st.columns(len(rows))
    for i, (c, r) in enumerate(zip(cols, rows)):
        best = "<span class='rl-tag best'>Best offer</span>" if i == 0 else ""
        c.markdown(
            f"""<div class="rl-card"><h4>{html.escape(r['name'])}</h4>
            <div class="rl-meta">{r['km']:.1f} km away</div>
            <div class="rl-price">₹{r['offer']:,.0f}</div>
            <div class="rl-meta">{r['share']*100:.0f}% of fair value</div>
            <div class="rl-bar"><i style="width:{r['share']*100:.0f}%"></i></div>
            <span class="rl-tag">{html.escape(r['spec'])}</span>{best}</div>""",
            unsafe_allow_html=True,
        )
    st.caption("Dealer names, locations and offers are sample data for the demo.")
    if st.button("Save to my impact", key="save_scrap") and fair > 0:
        try:
            add_impact_record(
                "scrap", kg=kg, amount=fair,
                details={"materials": df.to_dict(orient="records")},
            )
            st.toast("Saved to your account. You can see it on the Impact tab.")
        except Exception as e:
            st.error(f"Could not save impact data. Run the Supabase setup SQL first. ({type(e).__name__})")

# ----------------------------------------------------------------------------
# Book Loop
# ----------------------------------------------------------------------------
with tab_books:
    title_col, action_col = st.columns([4.5, 1.8], vertical_alignment="center")
    title_col.subheader("Pass on your books. Buy for less.")
    if action_col.button("＋ List a book", key="list_book_button", use_container_width=True):
        st.session_state.show_book_form = True

    f1, f2, f3 = st.columns([1, 1, 2])
    exam = f1.selectbox("Exam", ["All", "JEE", "NEET", "Boards"])
    subject = f2.selectbox("Subject", ["All", "Physics", "Chemistry", "Maths", "Biology"])
    q = f3.text_input("Search by title", placeholder="e.g. HC Verma")

    try:
        listings = load_book_listings()
        requested_ids = {str(row["listing_id"]) for row in load_book_requests()}
        owners_to_contact = {
            str(book["owner_id"]) for book in listings
            if str(book["id"]) in requested_ids
        }
        contact_by_owner = load_contact_profiles(owners_to_contact)
    except Exception as e:
        listings, requested_ids, contact_by_owner = [], set(), {}
        st.error(f"Could not load book listings or contacts. Check Supabase SQL and RLS policies. ({type(e).__name__})")

    shown = [
        row for row in listings
        if (exam == "All" or row["exam"] == exam)
        and (subject == "All" or row["subject"] == subject)
        and q.lower() in row["title"].lower()
    ]
    if not shown:
        st.info("No active listings match. List a book to add it to ReLoop.")

    for row_start in range(0, len(shown), 3):
        cols = st.columns(3, gap="medium")
        for c, book in zip(cols, shown[row_start:row_start + 3]):
            with c:
                saving = int(book["mrp"]) - int(book["price"])
                accent = {"Physics": MARIGOLD, "Chemistry": MINT, "Maths": CORAL, "Biology": "#9DB7FF"}.get(book["subject"], MARIGOLD)
                cnd = html.escape(book["condition"])
                cexam = html.escape(book["exam"])
                csubject = html.escape(book["subject"])
                st.markdown(
                    f"""<div class="rl-card" style="border-top:3px solid {accent}"><h4>{html.escape(book['title'])}</h4>
                    <div class="rl-meta">Listed by {html.escape(book['seller_name'])}</div>
                    <div style="margin:10px 0 2px 0"><span class="rl-price">₹{int(book['price'])}</span>
                    <span class="rl-strike">₹{int(book['mrp'])}</span></div>
                    <div class="rl-meta">You save ₹{saving}</div>
                    <span class="rl-tag">{cexam}</span><span class="rl-tag">{csubject}</span>
                    <span class="rl-tag">{cnd}</span></div>""",
                    unsafe_allow_html=True,
                )
                listing_id = str(book["id"])
                owner_id = str(book["owner_id"])
                if owner_id == current_user["id"]:
                    st.caption("Your active listing — see My books for sale below.")
                elif listing_id in requested_ids:
                    st.button("Request sent", key=f"req_{listing_id}", disabled=True)
                    contact = contact_by_owner.get(owner_id)
                    if contact:
                        owner_email = (contact.get("email") or "").strip()
                        owner_phone = (contact.get("phone") or "").strip()
                        st.info(f"Owner contact — {owner_email or 'email unavailable'} · {owner_phone or 'phone not added'}")
                        message = (
                            f"Hi {book['seller_name']}, I’m interested in your book, "
                            f"{book['title']}, listed on ReLoop for ₹{int(book['price'])}. "
                            "Is it still available? Could we discuss pickup or delivery?"
                        )
                        mail_subject = f"ReLoop book request: {book['title']}"
                        actions = st.columns(2)
                        phone_digits = "".join(ch for ch in owner_phone if ch.isdigit())
                        if len(phone_digits) >= 10:
                            wa_url = f"https://wa.me/{phone_digits}?text={quote(message, safe='')}"
                            actions[0].link_button("Message on WhatsApp", wa_url, use_container_width=True)
                        else:
                            actions[0].caption("WhatsApp needs the owner’s full phone number with country code.")
                        if owner_email:
                            gmail_url = "https://mail.google.com/mail/?view=cm&fs=1&" + urlencode(
                                {"to": owner_email, "su": mail_subject, "body": message}
                            )
                            actions[1].link_button("Compose email", gmail_url, use_container_width=True)
                        else:
                            actions[1].caption("Email draft unavailable: owner email is missing.")
                    else:
                        st.warning("Your request is saved, but the owner’s contact profile is unavailable.")
                elif st.button("Request this book", key=f"req_{listing_id}"):
                    try:
                        db_client().table("book_requests").insert({
                            "listing_id": listing_id, "requester_id": current_user["id"]
                        }).execute()
                        add_impact_record("book_request", amount=saving, details={"listing_id": listing_id})
                        st.success("Request sent. The owner’s email and phone will appear here.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Could not send this request. ({type(e).__name__})")

    # Catchy, always-visible action is beside the section title above.
    if st.session_state.get("show_book_form", False):
        with st.container(border=True):
            st.markdown("#### List a book for sale")
            with st.form("add_book", clear_on_submit=True):
                a, b_ = st.columns(2)
                title = a.text_input("Book title")
                seller = b_.text_input("Your name")
                c1, c2, c3, c4 = st.columns(4)
                ex = c1.selectbox("Exam", ["JEE", "NEET", "Boards"])
                sub = c2.selectbox("Subject", ["Physics", "Chemistry", "Maths", "Biology"])
                cond = c3.selectbox("Condition", ["Like new", "Good", "Fair"])
                mrp = c4.number_input("Original price (₹)", min_value=50, value=500, step=10)
                price = st.slider("Your price (₹)", 10, int(mrp), int(mrp * 0.4))
                publish = st.form_submit_button("Publish listing")
            if publish:
                if not title.strip() or not seller.strip():
                    st.error("Add the book title and your name.")
                else:
                    try:
                        saved_phone = current_user.get("phone")
                        if not saved_phone:
                            profile = load_contact_profiles([current_user["id"]]).get(current_user["id"], {})
                            saved_phone = profile.get("phone")
                        if not saved_phone:
                            st.error("Your account has no saved phone number. Add it to your account before listing a book.")
                            st.stop()
                        db_client().table("user_profiles").upsert({
                            "user_id": current_user["id"], "email": current_user["email"],
                            "phone": saved_phone,
                        }, on_conflict="user_id").execute()
                        db_client().table("book_listings").insert({
                            "owner_id": current_user["id"], "title": title.strip(),
                            "exam": ex, "subject": sub, "condition": cond,
                            "mrp": int(mrp), "price": int(price), "seller_name": seller.strip(),
                        }).execute()
                        st.session_state.show_book_form = False
                        st.success("Book listing saved to ReLoop.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Could not save the listing. Check Supabase SQL. ({type(e).__name__})")

    # Seller's active inventory appears below the books available to buy.
    st.markdown("### My books for sale")
    try:
        my_listings = load_my_book_listings()
        my_requests = load_requests_for_listings([row["id"] for row in my_listings])
    except Exception as e:
        my_listings, my_requests = [], []
        st.error(f"Could not load your listings and requests. ({type(e).__name__})")
    counts = {}
    for request in my_requests:
        key = str(request["listing_id"])
        counts[key] = counts.get(key, 0) + 1
    active_mine = [book for book in my_listings if book.get("status", "active") == "active"]

    if not active_mine:
        st.caption("You have no active book listings.")
    for book in active_mine:
        listing_id = str(book["id"])
        name_col, sold_col = st.columns([5, 1.2], vertical_alignment="center")
        name_col.markdown(
            f"**{html.escape(book['title'])}** · ₹{int(book['price'])} · {counts.get(listing_id, 0)} request(s)"
        )
        if sold_col.button("Sold", key=f"sold_{listing_id}", use_container_width=True):
            try:
                db_client().rpc("mark_book_sold", {"p_listing_id": int(book["id"])}).execute()
                st.success("Marked sold. It’s removed from active listings and you earned 100 ReLoop points.")
                st.rerun()
            except Exception as e:
                st.error(f"Could not mark the book sold. Run the latest sales migration SQL. ({type(e).__name__})")

# ----------------------------------------------------------------------------
# Sound Check
# ----------------------------------------------------------------------------
with tab_sound:
    st.subheader("Does it still run well?")
    st.write("Hold your phone near the fan or mixer while it runs, and record for 5 to 10 seconds.")
    res = None
    s1, s2 = st.columns([1, 1.2], gap="large")
    with s1:
        appliance = st.selectbox("Appliance", ["Ceiling or table fan", "Mixer grinder"])
        rec = st.audio_input("Record the sound")
        up = st.file_uploader("or upload a WAV file", type=["wav"])
        audio = rec or up

    with s2:
        if audio is None:
            st.markdown(
                f'<div class="rl-card"><h4>Waiting for a recording</h4>'
                f'<div class="rl-meta">Your verdict will show here: resell, repair or scrap.</div></div>',
                unsafe_allow_html=True,
            )
        else:
            try:
                res = analyze_audio(audio.getvalue())
                label, colour, why = verdict_for(res["fault"], res["silent"])
                c1_, c2_ = colour.split("|")
                st.markdown(
                    f'<div class="rl-verdict" style="background:linear-gradient(135deg,{c1_},{c2_});--glow:{c2_}"><div class="rl-meta" style="color:#fff;opacity:.8">{appliance}</div>'
                    f'<div class="v">{label}</div><p>{why}</p></div>',
                    unsafe_allow_html=True,
                )
                m1, m2, m3 = st.columns(3)
                m1.metric("Speed steadiness", f"{max(0, 100 - res['cv'] * 100):.0f}%")
                m2.metric("Sound cleanliness", f"{max(0, 100 - res['flat'] * 250):.0f}%")
                m3.metric("Rattle", f"{res['hf'] * 100:.0f}%")
                if st.button("Save this verdict", key="save_sound"):
                    try:
                        add_impact_record("appliance", details={"verdict": label, "appliance": appliance})
                        st.toast("Verdict saved to your account.")
                    except Exception as e:
                        st.error(f"Could not save the verdict. Run the Supabase setup SQL first. ({type(e).__name__})")
            except Exception:
                st.error("Could not read this audio. Record again, or upload a WAV file (16-bit).")

    if res is not None and not res["silent"]:
        step = max(1, len(res["x"]) // 1500)
        st.caption("Waveform")
        st.line_chart(pd.DataFrame({"amplitude": res["x"][::step]}), height=160, color=MARIGOLD)
    st.caption("Prototype scoring uses simple sound features. The full version swaps in a YAMNet model fine-tuned on working, noisy and dead appliance clips.")

# ----------------------------------------------------------------------------
# Impact
# ----------------------------------------------------------------------------
with tab_impact:
    st.subheader("What you have kept in use")
    try:
        records = load_impact_records()
        scrap_rows = [r for r in records if r["kind"] == "scrap"]
        book_rows = [r for r in records if r["kind"] == "book_request"]
        audio_rows = [r for r in records if r["kind"] == "appliance"]
        sale_rows = [r for r in records if r["kind"] == "book_sold"]
        points_total = sum(int((r.get("details") or {}).get("points", 0)) for r in sale_rows)
        sale_value_total = sum(float(r.get("amount") or 0) for r in sale_rows)
        kg_total = sum(float(r.get("kg") or 0) for r in scrap_rows)
        value_total = sum(float(r.get("amount") or 0) for r in scrap_rows)
        books_total = len(book_rows)
        saved_total = sum(float(r.get("amount") or 0) for r in book_rows)
        verdict_counts = {name: 0 for name in ("RESELL", "REPAIR", "SCRAP")}
        for record in audio_rows:
            verdict = (record.get("details") or {}).get("verdict")
            if verdict in verdict_counts:
                verdict_counts[verdict] += 1
    except Exception as e:
        st.error(f"Could not load your impact history. Run the Supabase setup SQL and check RLS policies. ({type(e).__name__})")
        kg_total = value_total = saved_total = 0
        books_total = 0
        points_total = 0
        sale_value_total = 0
        sale_rows = []
        verdict_counts = {name: 0 for name in ("RESELL", "REPAIR", "SCRAP")}

    k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
    k1.metric("Scrap valued", f"{kg_total:.1f} kg")
    k2.metric("Scrap value", f"₹{value_total:,.0f}")
    k3.metric("Books requested", books_total)
    k4.metric("Saved by juniors", f"₹{saved_total:,.0f}")
    k5.metric("Books sold", len(sale_rows))
    k6.metric("Sale value", f"₹{sale_value_total:,.0f}")
    k7.metric("ReLoop points", points_total)

    st.markdown("&nbsp;")
    st.markdown("**Appliance verdicts**")
    vdf = pd.DataFrame({"Verdict": list(verdict_counts), "Count": list(verdict_counts.values())}).set_index("Verdict")
    st.bar_chart(vdf, color=MARIGOLD, height=220)
    st.caption("Your totals are saved to your ReLoop account.")