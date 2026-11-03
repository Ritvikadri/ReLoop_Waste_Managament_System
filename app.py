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
import textwrap
import time
import wave
from urllib.parse import quote, urlencode
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(
    page_title="ReLoop",
    page_icon=os.path.join(os.path.dirname(__file__), "logo.png"),
    layout="wide",
)
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
        # Keep the account's saved display name available to listings and requests.
        st.session_state.auth_user["name"] = default_name

        with account_col:
            with st.popover("Account", use_container_width=True):
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

                st.divider()
                if st.button("Log out", key="account_logout", use_container_width=True):
                    try:
                        client.auth.sign_out()
                    finally:
                        for key in ("auth_user", "auth_token", "supabase_client"):
                            st.session_state.pop(key, None)
                        st.rerun()


@lru_cache(maxsize=1)
def reloop_logo_uri():
    """Load the bundled logo once for use in compact custom HTML UI elements."""
    logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
    with open(logo_path, "rb") as logo_file:
        return "data:image/png;base64," + base64.b64encode(logo_file.read()).decode("ascii")


def reloop_logo_mark(size=24):
    return (
        f'<img src="{reloop_logo_uri()}" alt="ReLoop" '
        f'style="width:{size}px;height:{size}px;object-fit:contain;display:block">'
    )


def show_auth_gate():
    client = get_supabase_client()
    user = st.session_state.get("auth_user")

    if user:
        st.sidebar.caption(f"Signed in: {user['email']}")
        # Attach this user's JWT to database API calls so RLS sees the user.
        token = st.session_state.get("auth_token")
        if token:
            client.postgrest.auth(token)
        return user

    splash_html = textwrap.dedent(f"""\
        <style>
        :root {{ color-scheme:dark!important; --primary-color:#F2B138!important; --background-color:#0A1514!important; --secondary-background-color:#111F1D!important; --text-color:#E9F0ED!important; }}
        html, body, .stApp, [data-testid="stAppViewContainer"] {{ background-color:#0A1514!important; color:#E9F0ED!important; }}
        .rl-auth-splash {{ position:fixed; inset:0; z-index:99999; display:grid; place-items:center;
          background:radial-gradient(circle at 50% 48%,#1D3932 0%,#0A1514 60%); color:#F4F7F5;
          animation:rl-splash-exit .85s ease-in-out 2.15s forwards; }}
        .rl-auth-splash-inner {{ display:flex; flex-direction:column; align-items:center; gap:13px; animation:rl-splash-arrive 1.1s cubic-bezier(.2,.8,.2,1) both; }}
        .rl-auth-splash img {{ width:128px; height:128px; object-fit:contain; filter:drop-shadow(0 0 22px rgba(145,195,85,.42)); animation:rl-logo-turn 1.7s cubic-bezier(.2,.7,.3,1) both; }}
        .rl-auth-splash strong {{ font-size:2.15rem; letter-spacing:.02em; opacity:0; animation:rl-title-in .55s ease .65s forwards; }}
        .rl-auth-splash span {{ color:#A8C7B8; font-size:.88rem; letter-spacing:.18em; text-transform:uppercase; opacity:0; animation:rl-title-in .5s ease .95s forwards; }}
        @keyframes rl-splash-arrive {{ from {{ transform:translateY(16px) scale(.84); opacity:0 }} to {{ transform:translateY(0) scale(1); opacity:1 }} }}
        @keyframes rl-logo-turn {{ from {{ transform:rotate(-100deg) scale(.65) }} to {{ transform:rotate(0) scale(1) }} }}
        @keyframes rl-title-in {{ to {{ opacity:1 }} }}
        @keyframes rl-splash-exit {{ to {{ opacity:0; visibility:hidden; pointer-events:none }} }}
        .stApp, [data-testid="stAppViewContainer"] {{
          background:radial-gradient(ellipse at 18% 8%,rgba(111,211,176,.12),transparent 34%),
                     radial-gradient(ellipse at 88% 22%,rgba(242,177,56,.10),transparent 30%),
                     linear-gradient(145deg,#0A1514 0%,#101D1B 52%,#0A1514 100%)!important;
        }}
        [data-testid="stMain"], .block-container {{ color:#E9F0ED!important; }}
        div[data-baseweb="select"] > div {{ background:#172A27!important; color:#E9F0ED!important; border-color:#25413D!important; }}
        div[data-baseweb="select"] input, div[data-baseweb="select"] [role="combobox"] {{ color:#E9F0ED!important; background:transparent!important; }}
        [data-baseweb="popover"], [data-baseweb="menu"], ul[role="listbox"], li[role="option"] {{ background:#172A27!important; color:#E9F0ED!important; }}
        .rl-auth-title {{ max-width:760px; box-sizing:border-box; text-align:center; margin:34px auto 16px; padding:26px 28px 18px;
          border:1px solid rgba(111,211,176,.20); border-radius:24px;
          background:radial-gradient(ellipse at 50% 0%,rgba(111,211,176,.11),transparent 68%),linear-gradient(145deg,rgba(23,42,39,.92),rgba(12,24,22,.92))!important;
          box-shadow:0 24px 70px -42px rgba(111,211,176,.42),inset 0 1px 0 rgba(255,255,255,.045); }}
        .rl-auth-title h2 {{ margin:0; color:#F4F7F5; font-size:clamp(2rem,4vw,3rem); font-weight:800; letter-spacing:-.035em; }}
        .rl-auth-title p {{ color:#A6BBB4; margin:9px 0 2px; font-size:1.08rem; }}
        .st-key-auth_mode_switch {{ box-sizing:border-box; max-width:520px; margin:4px auto 18px; padding:6px;
          border:1px solid rgba(111,211,176,.2); border-radius:19px; background:rgba(10,21,20,.78);
          box-shadow:0 16px 40px -30px rgba(111,211,176,.42); }}
        .st-key-auth_mode_login div.stButton > button, .st-key-auth_mode_login_active div.stButton > button,
        .st-key-auth_mode_signup div.stButton > button, .st-key-auth_mode_signup_active div.stButton > button {{
          box-sizing:border-box; width:100%; height:52px; min-height:52px; padding:10px 18px!important;
          border:1px solid transparent!important; border-radius:13px!important; background:transparent!important;
          color:#A9BDB7!important; box-shadow:none!important; font-size:1.02rem; font-weight:650;
          transform:none!important; outline:none!important; transition:background .16s ease,border-color .16s ease,color .16s ease;
        }}
        .st-key-auth_mode_login_active div.stButton > button, .st-key-auth_mode_signup_active div.stButton > button {{
          color:#FFFFFF!important; background:linear-gradient(145deg,rgba(255,255,255,.13),rgba(255,255,255,.06))!important;
          border:1px solid rgba(255,255,255,.88)!important; box-shadow:inset 0 1px 0 rgba(255,255,255,.1)!important;
        }}
        .st-key-auth_mode_login div.stButton > button:hover, .st-key-auth_mode_signup div.stButton > button:hover {{
          color:#FFFFFF!important; background:rgba(255,255,255,.055)!important; border-color:rgba(255,255,255,.25)!important;
        }}
        div[data-testid="stForm"] {{ box-sizing:border-box; max-width:760px; margin:12px auto 30px; padding:26px 28px;
          border:1px solid rgba(111,211,176,.20)!important; border-radius:22px;
          background:linear-gradient(145deg,rgba(23,42,39,.88),rgba(12,24,22,.92))!important;
          box-shadow:0 24px 70px -48px rgba(111,211,176,.32),inset 0 1px 0 rgba(255,255,255,.04); }}
        div[data-testid="stForm"] label {{ color:#E7EFEC!important; font-weight:600; }}
        div[data-testid="stForm"] input {{ color:#F4F7F5!important; background:#172A27!important; border-color:#25413D!important; color-scheme:dark!important; }}
        div[data-testid="stForm"] input::placeholder {{ color:#8FA8A2!important; }}
        div[data-testid="stForm"] .stFormSubmitButton > button {{ width:100%; min-height:58px; padding:16px 26px!important;
          border:1px solid rgba(255,255,255,.12)!important; border-radius:15px!important; color:#12332F!important;
          background:linear-gradient(135deg,#FFD45C,#F2B138)!important; font-size:1.12rem; font-weight:800;
          box-shadow:0 16px 34px -18px rgba(242,177,56,.68)!important; transition:filter .16s ease,box-shadow .16s ease,transform .16s ease; }}
        div[data-testid="stForm"] .stFormSubmitButton > button:hover {{ filter:brightness(1.08); transform:translateY(-1px);
          box-shadow:0 20px 42px -18px rgba(242,177,56,.78)!important; }}
        @media (max-width:640px) {{ div[data-testid="stForm"] {{ padding:20px 16px; }} .st-key-auth_mode_switch {{ margin-left:12px; margin-right:12px; }} }}
        @media (prefers-reduced-motion:reduce) {{ .rl-auth-splash,.rl-auth-splash * {{ animation-duration:.01ms!important; animation-delay:0ms!important; }} }}
        </style>
        <div class="rl-auth-splash"><div class="rl-auth-splash-inner">
          <img src="{reloop_logo_uri()}" alt="ReLoop logo"><strong>ReLoop</strong><span>Reuse · Resell · Recycle</span>
        </div></div>
    """)
    st.markdown(splash_html, unsafe_allow_html=True)
    st.markdown('<div class="rl-auth-title"><h2>Welcome to ReLoop</h2><p>Log in or create an account to continue</p></div>', unsafe_allow_html=True)
    st.session_state.setdefault("auth_mode", "Log in")
    with st.container(key="auth_mode_switch"):
        auth_login_col, auth_signup_col = st.columns(2, gap="small")
        login_key = "auth_mode_login_active" if st.session_state.auth_mode == "Log in" else "auth_mode_login"
        signup_key = "auth_mode_signup_active" if st.session_state.auth_mode == "Sign up" else "auth_mode_signup"
        if auth_login_col.button("Log in", key=login_key, use_container_width=True):
            st.session_state.auth_mode = "Log in"
            st.rerun()
        if auth_signup_col.button("Sign up", key=signup_key, use_container_width=True):
            st.session_state.auth_mode = "Sign up"
            st.rerun()

    if st.session_state.auth_mode == "Log in":
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

    else:
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

 :root {{ color-scheme: dark !important; --primary-color:{MARIGOLD} !important; --background-color:{BG} !important; --secondary-background-color:{SURFACE} !important; --text-color:{TEXT} !important; }}
html, body, [class*="css"], .stApp {{ font-family: 'DM Sans', sans-serif !important; color: {TEXT} !important; color-scheme: dark !important; }}
.stApp {{ --primary-color:{MARIGOLD} !important; --background-color:{BG} !important; --secondary-background-color:{SURFACE} !important; --text-color:{TEXT} !important; }}
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"], section.main {{
  background:
    radial-gradient(1100px 520px at 95% -8%, rgba(242,177,56,.10), transparent 60%),
    radial-gradient(900px 500px at -5% 105%, rgba(111,211,176,.09), transparent 60%),
    {BG} !important;
}}
[data-testid="stSidebar"], [data-testid="stHeader"] {{ background-color:{BG} !important; color:{TEXT} !important; }}
[data-testid="stMainBlockContainer"], .block-container {{ color:{TEXT} !important; }}
[data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] *, label, label * {{ color:{TEXT} !important; }}
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

/* four-step circular flow */
.rl-flow {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:18px 0 28px; }}
.rl-flow div {{ position:relative; isolation:isolate; overflow:hidden; min-height:120px; padding:19px 20px 17px;
  border:1px solid transparent; border-radius:18px;
  background:linear-gradient(145deg,rgba(20,39,36,.98),rgba(12,25,23,.98)) padding-box,
    linear-gradient(135deg,rgba(111,211,176,.72),rgba(242,177,56,.68),rgba(111,211,176,.18)) border-box;
  box-shadow:0 16px 36px -28px rgba(0,0,0,.9),inset 0 1px 0 rgba(255,255,255,.055);
  transition:transform .2s ease,box-shadow .2s ease; }}
.rl-flow div::after {{ content:""; position:absolute; z-index:-1; width:130px; height:130px; right:-62px; top:-72px;
  border-radius:50%; background:radial-gradient(circle,rgba(111,211,176,.14),transparent 68%); }}
.rl-flow div:hover {{ transform:translateY(-4px); box-shadow:0 20px 42px -26px rgba(111,211,176,.24),inset 0 1px 0 rgba(255,255,255,.08); }}
.rl-flow .rl-step {{ display:block; color:{MINT}; font-size:.7rem; font-weight:700; letter-spacing:.16em; margin-bottom:11px; }}
.rl-flow b {{ display:block; font-family:'Bricolage Grotesque'; font-size:1.14rem; color:{TEXT}; margin-bottom:5px; }}
.rl-flow span:last-child {{ color:{MUTE}; font-size:.9rem; }}

/* ---------- dealer cards: equal size, button pinned to the bottom ---------- */
.rl-dealer-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 18px; align-items: stretch; margin: 6px 0 14px 0; }}
.rl-dealer {{ box-sizing: border-box; display: flex; flex-direction: column; min-height: 340px; padding: 22px 22px 20px 22px;
  background: linear-gradient(180deg, {SURFACE2} 0%, {SURFACE} 100%); border: 1px solid {LINE}; border-radius: 20px;
  box-shadow: 0 18px 40px -26px rgba(0,0,0,.8), inset 0 1px 0 rgba(255,255,255,.04);
  transition: transform .18s ease, border-color .18s ease; }}
.rl-dealer:hover {{ transform: translateY(-3px); border-color: #3B6A63; }}
.rl-dealer h4 {{ margin: 0 0 6px 0; min-height: 2.5em; font-family: 'Bricolage Grotesque', sans-serif; font-size: 1.12rem; color: {TEXT}; line-height: 1.25; }}
.rl-dealer-price-row {{ display:flex; align-items:center; justify-content:space-between; gap:12px; min-height:38px; }}
.rl-location-link {{ display:inline-flex; flex:0 0 38px; align-items:center; justify-content:center; width:38px; height:38px;
  color:{MINT}; border:1px solid rgba(111,211,176,.30); border-radius:12px; background:rgba(111,211,176,.08);
  text-decoration:none !important; transition:background .15s ease, transform .15s ease, border-color .15s ease; }}
.rl-location-link:hover {{ color:{TEXT}; background:rgba(111,211,176,.2); border-color:{MINT}; transform:translateY(-2px); }}
.rl-location-link svg {{ width:20px; height:20px; display:block; }}
.rl-dealer-tags {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 14px 0 18px 0; }}
.rl-dealer-tags .rl-tag {{ margin: 0; }}
.rl-wa-btn {{ margin-top: auto; display: flex; align-items: center; justify-content: center; gap: 8px; text-align: center;
  text-decoration: none !important; padding: 12px 14px; border-radius: 12px; font-weight: 700; font-size: .95rem;
  color: #062B1A !important; background: linear-gradient(135deg, #4BE08C, #25D366);
  box-shadow: 0 10px 24px -12px rgba(37,211,102,.7); transition: transform .12s ease, filter .12s ease; }}
.rl-wa-btn::before {{ content: "💬"; }}
.rl-wa-btn:hover {{ filter: brightness(1.07); transform: translateY(-1px); color: #062B1A !important; }}
@media (max-width: 1000px) {{ .rl-dealer-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
@media (max-width: 560px) {{
  .rl-dealer-grid {{ grid-template-columns: 1fr; }}
  .rl-dealer {{ min-height: 0; }}
  .rl-dealer h4 {{ min-height: 0; }}
}}

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
  margin-bottom: 14px; box-shadow: 0 24px 60px -24px rgba(242,177,56,.62), 0 0 34px -22px rgba(242,177,56,.5); }}
.rl-value .n {{ font-family:'Bricolage Grotesque'; font-size: 2.7rem; font-weight: 800; line-height:1.05; margin-top: 4px; }}

.rl-verdict {{ border-radius: 24px; padding: 28px 32px; color: #fff; border: 1px solid rgba(255,255,255,.14);
  box-shadow: 0 30px 70px -28px var(--glow, rgba(0,0,0,.6)); }}
.rl-verdict .v {{ font-family:'Bricolage Grotesque'; font-size: 3rem; font-weight: 800; line-height: 1; margin-top: 6px; }}
.rl-verdict p {{ margin: 10px 0 0 0; opacity: .92; max-width: 40ch; }}

/* ---------- impact dashboard ---------- */
.rl-imp-grid {{ display: grid; gap: 24px; margin: 0 0 26px 0; align-items: stretch; }}
.rl-imp-grid.g-head {{ grid-template-columns: 2fr 1fr; align-items: stretch; }}
.rl-imp-grid.g-three {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
.rl-imp-grid.g-detail {{ grid-template-columns: 1fr 1.4fr; }}
.rl-imp-grid > div {{ box-sizing: border-box; min-width: 0; }}
.rl-imp-hero {{ position: relative; overflow: hidden; box-sizing: border-box; min-height: 0; border-radius: 26px; padding: 26px 30px;
  background:
    radial-gradient(520px 260px at 100% 0%, rgba(242,177,56,.26), transparent 62%),
    radial-gradient(440px 280px at 0% 100%, rgba(111,211,176,.16), transparent 62%),
    linear-gradient(160deg, #16302C 0%, #0D1B19 100%);
  border: 1px solid {LINE}; box-shadow: 0 30px 70px -34px rgba(0,0,0,.85), inset 0 1px 0 rgba(255,255,255,.05); }}
.rl-imp-eyebrow {{ color: {MUTE}; font-size: .78rem; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; }}
.rl-imp-big {{ font-family: 'Bricolage Grotesque', sans-serif; font-size: 4rem; font-weight: 800; line-height: 1.02; margin: 10px 0 8px 0;
  background: linear-gradient(90deg, #FFD45C 0%, {MARIGOLD} 45%, {MINT} 100%); -webkit-background-clip: text; background-clip: text; color: transparent;
  font-variant-numeric: tabular-nums; }}
.rl-imp-sub {{ color: #B7C9C4; max-width: 46ch; line-height: 1.5; }}
.rl-imp-split {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 16px; }}
.rl-imp-split span {{ background: rgba(255,255,255,.05); border: 1px solid {LINE}; border-radius: 12px; padding: 8px 14px; color: {MUTE}; font-size: .9rem; }}
.rl-imp-split b {{ color: {TEXT}; font-weight: 700; margin-right: 4px; font-variant-numeric: tabular-nums; }}

.rl-imp-level {{ box-sizing: border-box; min-height: 0; border-radius: 26px; padding: 24px 26px; border: 1px solid {LINE};
  background: linear-gradient(180deg, {SURFACE2} 0%, {SURFACE} 100%); box-shadow: 0 18px 40px -26px rgba(0,0,0,.8), inset 0 1px 0 rgba(255,255,255,.04); }}
.rl-imp-levelrow {{ display: flex; align-items: center; gap: 14px; margin: 13px 0 14px 0; }}
.rl-imp-badge {{ width: 60px; height: 60px; flex: none; border-radius: 18px; display: grid; place-items: center; font-size: 1.9rem;
  background: rgba(242,177,56,.14); border: 1px solid rgba(242,177,56,.4); }}
.rl-imp-pts {{ font-family: 'Bricolage Grotesque', sans-serif; font-size: 2.4rem; font-weight: 800; line-height: 1; color: {MARIGOLD}; font-variant-numeric: tabular-nums; }}
.rl-imp-lvl {{ color: {TEXT}; font-weight: 600; margin-top: 4px; }}
.rl-imp-progress {{ height: 10px; border-radius: 99px; background: #0A1514; overflow: hidden; }}
.rl-imp-progress i {{ display: block; height: 100%; border-radius: 99px; background: linear-gradient(90deg, {MINT}, {MARIGOLD}); }}
.rl-imp-next {{ color: {TEXT}; font-size: .9rem; margin-top: 8px; }}
.rl-imp-tip {{ color: {MUTE}; font-size: .78rem; line-height:1.4; margin-top: 10px; }}
.rl-imp-contrib {{ margin-top:9px; padding:0; color:{MUTE}; font-size:.8rem; line-height:1.35; }}
.rl-imp-contrib b {{ color:{TEXT}; }}
.rl-imp-benchmarks {{ display:flex; align-items:center; flex-wrap:wrap; gap:3px 8px; margin-top:9px; }}
.rl-imp-benchmark {{ display:inline-flex; align-items:center; gap:3px; min-width:0; padding:2px 0; border-radius:0;
  background:transparent; border:0; color:{MUTE}; font-size:.64rem; white-space:nowrap; }}
.rl-imp-benchmark b {{ color:{TEXT}; font-weight:650; }}
.rl-imp-benchmark small {{ color:{MUTE}; font-size:.61rem; }}
.rl-imp-benchmark.current {{ text-decoration:underline; text-decoration-color:rgba(242,177,56,.75); text-underline-offset:3px; }}
.rl-imp-benchmark.current b {{ color:{MARIGOLD}; }}

.rl-imp-panel {{ box-sizing: border-box; border-radius: 22px; padding: 22px 24px; border: 1px solid {LINE};
  background: linear-gradient(180deg, {SURFACE2} 0%, {SURFACE} 100%);
  background: radial-gradient(340px 150px at 0% 0%, color-mix(in srgb, var(--accent, {MARIGOLD}) 16%, transparent), transparent 72%),
              linear-gradient(180deg, {SURFACE2} 0%, {SURFACE} 100%);
  box-shadow: 0 18px 40px -26px rgba(0,0,0,.8), inset 0 1px 0 rgba(255,255,255,.04); }}
.rl-imp-panel-head {{ display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }}
.rl-imp-icon {{ width: 42px; height: 42px; flex: none; border-radius: 13px; display: grid; place-items: center; font-size: 1.3rem; }}
.rl-imp-icon img {{ width:26px !important; height:26px !important; }}
.rl-imp-panel h4 {{ margin: 0; font-family: 'Bricolage Grotesque', sans-serif; font-size: 1.15rem; color: {TEXT}; line-height: 1.2; }}
.rl-imp-panel-head small {{ color: {MUTE}; font-size: .85rem; }}
.rl-imp-stat {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; padding: 12px 0; border-bottom: 1px dashed #2A4A45; }}
.rl-imp-stat:last-child {{ border-bottom: 0; }}
.rl-imp-stat span {{ color: {MUTE}; font-size: .95rem; }}
.rl-imp-stat b {{ font-family: 'Bricolage Grotesque', sans-serif; font-size: 1.55rem; font-weight: 800; color: {TEXT}; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.rl-imp-barrow {{ margin: 16px 0 4px 0; }}
.rl-imp-barlabel {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 7px; font-size: .95rem; }}
.rl-imp-barlabel span {{ display: flex; align-items: center; gap: 8px; color: {TEXT}; font-weight: 600; }}
.rl-imp-barlabel .d {{ width: 9px; height: 9px; border-radius: 50%; display: inline-block; }}
.rl-imp-barlabel em {{ color: {MUTE}; font-style: normal; font-variant-numeric: tabular-nums; }}
.rl-imp-track {{ height: 12px; border-radius: 99px; background: #0A1514; overflow: hidden; }}
.rl-imp-track i {{ display: block; height: 100%; border-radius: 99px; }}
.rl-imp-empty {{ color: {MUTE}; font-size: .95rem; line-height: 1.5; padding: 18px 0 6px 0; }}
.rl-imp-item {{ display: flex; align-items: center; gap: 14px; padding: 12px 0; border-bottom: 1px solid {LINE}; }}
.rl-imp-item:last-child {{ border-bottom: 0; }}
.rl-imp-dot {{ width: 40px; height: 40px; flex: none; border-radius: 12px; display: grid; place-items: center; font-size: 1.15rem; }}
.rl-imp-dot img {{ width:24px !important; height:24px !important; }}
.rl-imp-item .t {{ flex: 1; min-width: 0; }}
.rl-imp-item .t b {{ display: block; color: {TEXT}; font-weight: 600; }}
.rl-imp-item .t span {{ color: {MUTE}; font-size: .85rem; }}
.rl-imp-amt {{ font-family: 'Bricolage Grotesque', sans-serif; font-weight: 800; font-size: 1.05rem; white-space: nowrap; }}
@media (max-width: 900px) {{
  .rl-imp-grid.g-head, .rl-imp-grid.g-three, .rl-imp-grid.g-detail {{ grid-template-columns: 1fr; gap: 18px; }}
  .rl-imp-benchmarks {{ gap: 3px; }}
  .rl-imp-benchmark {{ font-size: .62rem; padding: 2px 0; }}
  .rl-imp-benchmark small {{ font-size: .58rem; }}
  .rl-imp-big {{ font-size: 2.8rem; }}
  .rl-imp-hero, .rl-imp-level {{ min-height: 0; }}
}}

/* ---------- workspace label row (sits directly under the logo) ---------- */
.rl-workspace-bar {{ display:flex; justify-content: space-between; align-items: center; gap: 16px; margin: 0 0 14px 0; padding: 0 4px; }}
.rl-eyebrow {{ color: {MINT}; font-size: .82rem; font-weight: 800; letter-spacing: .22em; text-transform: uppercase; }}
.rl-hint {{ color: {MUTE}; font-size: 1rem; }}
@media (max-width: 640px) {{
  .rl-workspace-bar {{ flex-direction: column; align-items: flex-start; gap: 4px; }}
}}

/* ---------- compact, centered section navigation in one steady frame ---------- */
.st-key-section_nav {{
  box-sizing:border-box; max-width:1160px; margin:0 auto 22px; padding:5px 8px;
  border:1px solid rgba(111,211,176,.22); border-radius:20px;
  background:linear-gradient(145deg,rgba(19,37,34,.42),rgba(12,24,22,.58));
}}
.st-key-nav_scrap_vision div.stButton > button, .st-key-nav_book_loop div.stButton > button,
.st-key-nav_sound_check div.stButton > button, .st-key-nav_impact div.stButton > button {{
  box-sizing:border-box; width:100%; height:44px; min-height:44px; padding:7px 12px !important;
  border:1px solid transparent !important; border-radius:14px !important;
  color:#A9BDB7 !important; background:transparent !important; box-shadow:none !important;
  font-size:.96rem; font-weight:600; line-height:1.2; white-space:nowrap;
  transform:none !important; outline:none !important;
  transition:background .16s ease,border-color .16s ease,color .16s ease;
}}
.st-key-nav_scrap_vision div.stButton > button:hover, .st-key-nav_book_loop div.stButton > button:hover,
.st-key-nav_sound_check div.stButton > button:hover, .st-key-nav_impact div.stButton > button:hover {{
  color:#FFFFFF !important; background:rgba(255,255,255,.055) !important;
  border-color:rgba(255,255,255,.28) !important; transform:none !important; box-shadow:none !important;
}}
.st-key-nav_scrap_vision_active div.stButton > button, .st-key-nav_book_loop_active div.stButton > button,
.st-key-nav_sound_check_active div.stButton > button, .st-key-nav_impact_active div.stButton > button {{
  color:#FFFFFF !important; background:rgba(255,255,255,.085) !important;
  border:1px solid rgba(255,255,255,.88) !important; box-shadow:inset 0 1px 0 rgba(255,255,255,.10) !important;
  font-weight:650; transform:none !important; outline:none !important;
}}
@media (max-width: 640px) {{
  .st-key-section_nav {{ padding:4px; border-radius:16px; }}
  .st-key-nav_scrap_vision div.stButton > button, .st-key-nav_book_loop div.stButton > button,
  .st-key-nav_sound_check div.stButton > button, .st-key-nav_impact div.stButton > button {{ height:42px; min-height:42px; padding:6px 3px !important; font-size:.75rem; white-space:normal; }}
}}

.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
  border-radius: 12px; border: 0; background: {MARIGOLD}; color: {SPRUCE}; font-weight: 700; padding: 9px 20px;
  box-shadow: 0 10px 24px -12px rgba(242,177,56,.7); transition: transform .12s ease, filter .12s ease; }}
.stButton > button:hover, .stFormSubmitButton > button:hover {{ filter: brightness(1.08); transform: translateY(-1px); color: {SPRUCE}; border: 0; }}
.stButton > button:focus-visible {{ outline: 3px solid {MINT}; outline-offset: 2px; }}
.stButton > button:disabled {{ background: {SURFACE2}; color: {MUTE}; box-shadow: none; }}
.st-key-save_scrap {{ margin-top: 12px; margin-bottom: 8px; }}
.st-key-save_scrap div.stButton > button {{ min-height: 60px !important; border-radius: 16px !important;
  background: linear-gradient(135deg, #FFD45C, #F2B138) !important; color: {SPRUCE} !important;
  -webkit-text-fill-color: {SPRUCE} !important; opacity: 1 !important;
  box-shadow: 0 0 24px rgba(242,177,56,.30), 0 12px 32px rgba(242,177,56,.28), inset 0 1px 0 rgba(255,255,255,.22) !important;
  transition: transform .18s ease, filter .18s ease, box-shadow .18s ease !important; }}
.st-key-save_scrap div.stButton > button:hover,
.st-key-save_scrap div.stButton > button:active,
.st-key-save_scrap div.stButton > button:focus-visible {{ background: linear-gradient(135deg, #FFE078, #F7BC45) !important;
  color: {SPRUCE} !important; -webkit-text-fill-color: {SPRUCE} !important; filter: brightness(1.06) !important; transform: translateY(-2px);
  box-shadow: 0 0 34px rgba(242,177,56,.46), 0 16px 38px rgba(242,177,56,.34), inset 0 1px 0 rgba(255,255,255,.28) !important; }}
.st-key-save_scrap div.stButton > button:disabled {{ background: {SURFACE2} !important; color: {MUTE} !important;
  -webkit-text-fill-color: {MUTE} !important; box-shadow: none !important; opacity: .7 !important; transform: none !important; }}
.st-key-reverify_photos div.stButton > button {{ background: linear-gradient(135deg, #FFD45C, #F2B138) !important;
  color: {SPRUCE} !important; -webkit-text-fill-color: {SPRUCE} !important; opacity: 1 !important;
  box-shadow: 0 10px 24px -10px rgba(242,177,56,.72) !important; transition: transform .16s ease, filter .16s ease, box-shadow .16s ease !important; }}
.st-key-reverify_photos div.stButton > button:hover,
.st-key-reverify_photos div.stButton > button:active,
.st-key-reverify_photos div.stButton > button:focus-visible {{ background: linear-gradient(135deg, #FFE078, #F7BC45) !important;
  color: {SPRUCE} !important; -webkit-text-fill-color: {SPRUCE} !important; filter: brightness(1.06) !important;
  transform: translateY(-1px); box-shadow: 0 0 26px rgba(242,177,56,.48), 0 12px 28px -8px rgba(242,177,56,.62) !important; }}
.st-key-list_book_button button {{
  --list-glow-alpha: .20; --list-glow-blur: 12px; --list-glow-spread: 0px;
  --list-shadow-alpha: .14; --list-shadow-blur: 24px;
  position:relative;
  background: linear-gradient(135deg, #FFD45C, #F2B138);
  font-weight: 800; border: 0;
  box-shadow: 0 0 var(--list-glow-blur) var(--list-glow-spread) rgba(242,177,56,var(--list-glow-alpha)),
              0 8px var(--list-shadow-blur) rgba(242,177,56,var(--list-shadow-alpha));
  transition: box-shadow .15s ease, transform .15s ease, filter .15s ease;
}}
.st-key-list_book_button button:hover {{ filter: brightness(1.12); transform: translateY(-2px); }}
.st-key-refresh_my_book_requests button,
.st-key-refresh_my_book_requests button:hover,
.st-key-refresh_my_book_requests button:active,
.st-key-refresh_my_book_requests button:focus-visible {{
  min-height:50px; color:#12332F !important; background:linear-gradient(135deg,#FFD45C,#F2B138) !important;
  border:1px solid rgba(255,255,255,.14) !important; border-radius:13px !important;
  box-shadow:0 10px 24px -12px rgba(242,177,56,.55) !important;
  filter:none !important; opacity:1 !important; transform:none !important; outline:none !important;
}}


[data-testid="stMetric"] {{ background: linear-gradient(180deg, {SURFACE2}, {SURFACE}); border: 1px solid {LINE}; border-radius: 18px; padding: 16px 20px; }}
[data-testid="stMetricValue"] {{ font-family: 'Bricolage Grotesque'; font-weight: 800; color: {MARIGOLD}; }}
[data-testid="stMetricLabel"] p {{ color: {MUTE}; }}

[data-testid="stSelectbox"] [data-baseweb="select"] > div,
[data-testid="stMultiSelect"] [data-baseweb="select"] > div,
[data-baseweb="select"] > div, [data-baseweb="input"] > div,
.stTextInput input, .stNumberInput input, .stTextArea textarea {{
  background:{SURFACE} !important; color:{TEXT} !important; border-color:{LINE} !important; border-radius:12px !important; color-scheme:dark !important;
}}
[data-testid="stSelectbox"] [data-baseweb="select"] input,
[data-testid="stSelectbox"] [data-baseweb="select"] [role="combobox"],
[data-testid="stMultiSelect"] [data-baseweb="select"] input {{ background:transparent !important; color:{TEXT} !important; }}
[data-testid="stSelectbox"] [data-baseweb="select"] {{ background:{SURFACE} !important; color:{TEXT} !important; }}
[data-testid="stSelectbox"] [data-baseweb="select"] * {{ background-color:transparent !important; color:{TEXT} !important; }}
[data-testid="stSelectbox"] [data-baseweb="select"] > div {{ background:{SURFACE} !important; }}
[data-testid="stSelectbox"] [data-baseweb="select"]:focus-within,
[data-testid="stMultiSelect"] [data-baseweb="select"]:focus-within,
.stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus {{ border-color:{MINT} !important; box-shadow:0 0 0 1px rgba(111,211,176,.32) !important; }}
[data-baseweb="popover"], [data-baseweb="menu"], ul[role="listbox"],
li[role="option"], [role="option"] {{ background:{SURFACE2} !important; color:{TEXT} !important; }}
[data-testid="stFileUploaderDropzone"] {{ background:{SURFACE} !important; color:{TEXT} !important; border:1.5px dashed #3B6A63; border-radius:16px; }}
[data-testid="stExpander"] {{ background:{SURFACE} !important; color:{TEXT} !important; border:1px solid {LINE}; border-radius:16px; }}
[data-testid="stForm"] {{ background:{SURFACE} !important; color:{TEXT} !important; border:1px solid {LINE}; border-radius:16px; }}
[data-testid="stAlert"] {{ color:{TEXT} !important; }}
[data-testid="stDataFrame"], [data-testid="stImage"] img {{ border-radius: 16px; }}
[data-testid="stCaptionContainer"] {{ color: {MUTE}; }}
div[data-testid="stPopover"] > button {{ color:{TEXT} !important; background:{SURFACE2} !important; border:1px solid {LINE} !important; }}
div[data-testid="stPopover"] > button:hover {{ color:{TEXT} !important; background:{SURFACE2} !important; border-color:rgba(255,255,255,.42) !important; }}
button[data-testid="stPopoverButton"] {{ color:{TEXT} !important; background:{SURFACE2} !important; border:1px solid {LINE} !important; }}
button[data-testid="stPopoverButton"]:hover {{ color:{TEXT} !important; background:{SURFACE2} !important; border-color:rgba(255,255,255,.42) !important; }}

@media (max-width: 900px) {{
  .rl-hero {{ grid-template-columns: 1fr; padding: 30px 24px; }}
  .rl-hero h1 {{ font-size: 2.4rem; }}
  .rl-flow {{ grid-template-columns: 1fr 1fr; }}
}}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# Main navigation: app-controlled buttons avoid Streamlit's built-in tab underline.
# ----------------------------------------------------------------------------
st.markdown(
    '<div class="rl-workspace-bar"><span class="rl-eyebrow">Your ReLoop workspace</span>'
    '<span class="rl-hint"></span></div>',
    unsafe_allow_html=True,
)
st.session_state.setdefault("active_section", "Scrap Vision")
active_section = st.session_state.active_section
with st.container(key="section_nav"):
    nav_scrap, nav_books, nav_sound, nav_impact = st.columns(4, gap="small")
    for nav_col, label, slug in [
        (nav_scrap, "Scrap Vision", "scrap_vision"),
        (nav_books, "Book Loop", "book_loop"),
        (nav_sound, "Sound Check", "sound_check"),
        (nav_impact, "Impact", "impact"),
    ]:
        widget_key = f"nav_{slug}_active" if active_section == label else f"nav_{slug}"
        if nav_col.button(label, key=widget_key, use_container_width=True):
            st.session_state.active_section = label
            st.rerun()

# ----------------------------------------------------------------------------
# Data (sample data for the prototype; replace with Supabase tables later)
# ----------------------------------------------------------------------------
RATES = {  # illustrative Rs per kg, localize city by city
    "Newspaper / paper": 12, "Cardboard": 8, "Plastic (mixed)": 15, "Iron": 28,
    "Steel": 35, "Aluminium": 105, "Brass": 400, "Copper": 650,
    "Glass": 2, "E-waste (mixed)": 40,
}

# OWNER-ONLY AREA DIRECTORY — add a neighborhood here before assigning dealers to it.
# Format: "Neighborhood name": (latitude, longitude). Use the neighborhood's center.
AREAS = {
    "WhiteField": (12.9082, 77.5646),
    "Banashankari": (12.9255, 77.5468),
    "JP Nagar": (12.9063, 77.5857),
    "Jayanagar": (12.9308, 77.5838),
}

# OWNER-ONLY KABADIWALA DIRECTORY — edit this section in app.py.
# This is deliberately a Python configuration (there is no public edit form),
# so only someone who can edit and redeploy the app's source code can change it.
#
# Put each place's kabadiwalas under that place's exact name. Dealers in one
# place will NOT appear when a user selects a different place.
# To add a place, add it to AREAS above, then create a matching key below.
# To add a kabadiwala, copy one dealer entry inside the correct place's list.
#   name:       shop / kabadiwala name shown on the card
#   lat, lon:   dealer's map coordinates in decimal degrees
#   share:      estimated fair value fraction (0.88 means 88%)
#   speciality: short materials description shown on the card
#   phone:      that dealer's individual WhatsApp number with country code
#               (example format: "+919876543210"). Leave blank until confirmed.
#   maps_url:   paste the shop's Google Maps share link between the quotes.
#               If blank, ReLoop opens Google Maps at the coordinates above.
#
# Replace the sample dealer entries and blank phone numbers with real, verified
# details. When phone is blank, the app won't create a broken WhatsApp link.
# Copy this example entry into the list for the right place, then replace values:
# {
#     "name": "Shop name",
#     "lat": 12.9000, "lon": 77.5600,  # Use this shop's map coordinates.
#     "share": 0.90,                   # 0.90 means an estimated 90% of fair value.
#     "speciality": "Paper, plastic",
#     "phone": "+919876543210",       # Enter this dealer's own WhatsApp number.
#     "maps_url": "https://maps.app.goo.gl/...",  # Paste this shop's Google Maps link.
# },
DEALERS_BY_AREA = {
    "WhiteField": [
        {
            "name": "SR SCRAP DEALERS",
            "lat": 12.9140, "lon": 77.5600,
            "share": 0.46,
            "speciality": "All household scrap",
            "phone": "+919916410931", 
            "maps_url": "https://www.google.com/maps/place/SR+SCRAP+DEALERS/@12.981355,77.3448391,11z/data=!4m10!1m2!2m1!1sscrap+shop+in+whitefield!3m6!1s0x3bae0d62e83a3133:0x485d89d59a08d7e9!8m2!3d12.9711442!4d77.7535249!15sChhzY3JhcCBzaG9wIGluIHdoaXRlZmllbGRaGiIYc2NyYXAgc2hvcCBpbiB3aGl0ZWZpZWxkkgESc2NyYXBfbWV0YWxfZGVhbGVymgFEQ2k5RFFVbFJRVU52WkVOb2RIbGpSamx2VDI1d1JWbFZiekZNVjJSVFRVZDRSMVpGYUdGVFJXaFNWMnBvUzFwV1JSQULgAQD6AQQIABBA!16s%2Fg%2F11j384l71g?entry=ttu&g_ep=EgoyMDI2MTAyOC4wIKXMDSoASAFQAw%3D%3D",  # Paste Ramesh Traders' Google Maps share link here.
        },
        {
            "name": "MSS SCRAP DEALERS",
            "lat": 12.9140, "lon": 77.5600,
            "share": 0.96,
            "speciality": "Plastic, Metal, All household scrap",
            "phone": "+917010623151", 
            "maps_url": "https://www.google.com/maps/place/MSS+E-WASTE+AND+ALL+KIND+OF+SCRAP+TRADERS/@12.981355,77.3448391,11z/data=!4m10!1m2!2m1!1sscrap+shop+in+whitefield!3m6!1s0x3bae138b149656a3:0x910ccb5c60acc025!8m2!3d12.9764637!4d77.7561391!15sChhzY3JhcCBzaG9wIGluIHdoaXRlZmllbGRaGiIYc2NyYXAgc2hvcCBpbiB3aGl0ZWZpZWxkkgEbcmVjeWNsaW5nX2Ryb3Bfb2ZmX2xvY2F0aW9u4AEA!16s%2Fg%2F11n570dmzg?entry=ttu&g_ep=EgoyMDI2MTAyOC4wIKXMDSoASAFQAw%3D%3Dhttps://www.google.com/maps/place/MSS+E-WASTE+AND+ALL+KIND+OF+SCRAP+TRADERS/@12.981355,77.3448391,11z/data=!4m10!1m2!2m1!1sscrap+shop+in+whitefield!3m6!1s0x3bae138b149656a3:0x910ccb5c60acc025!8m2!3d12.9764637!4d77.7561391!15sChhzY3JhcCBzaG9wIGluIHdoaXRlZmllbGRaGiIYc2NyYXAgc2hvcCBpbiB3aGl0ZWZpZWxkkgEbcmVjeWNsaW5nX2Ryb3Bfb2ZmX2xvY2F0aW9u4AEA!16s%2Fg%2F11n570dmzg?entry=ttu&g_ep=EgoyMDI2MTAyOC4wIKXMDSoASAFQAw%3D%3D",  # Paste Ramesh Traders' Google Maps share link here.
        },
        {
            "name": "ABM SCRAP DEALER",
            "lat": 12.9140, "lon": 77.5600,
            "share": 0.97,
            "speciality": "Metal, All household scrap",
            "phone": "+919036340402", 
            "maps_url": "https://www.google.com/maps/place/ABM+SCRAP+DEALER/@12.981355,77.3448391,11z/data=!4m10!1m2!2m1!1sscrap+shop+in+whitefield!3m6!1s0x3bae117871b1c3d3:0x8806323a40d78d6!8m2!3d12.9725877!4d77.7546121!15sChhzY3JhcCBzaG9wIGluIHdoaXRlZmllbGRaGiIYc2NyYXAgc2hvcCBpbiB3aGl0ZWZpZWxkkgEOc2NyYXBfbWVyY2hhbnTgAQA!16s%2Fg%2F11n3z3dd_f?entry=ttu&g_ep=EgoyMDI2MTAyOC4wIKXMDSoASAFQAw%3D%3D",  # Paste Ramesh Traders' Google Maps share link here.
        },
    ],

    
    "Banashankari": [
        {
            "name": "AMS Scrap Mercant",
            "lat": 12.9140, "lon": 77.5600,
            "share": 0.88,
            "speciality": "Metal, Steel, Copper",
            "phone": "+919845647024", 
            "maps_url": "https://www.google.com/maps/place/AMS+Scrap+Merchant+-+Metal+Steel+Scrap+and+copper+buyers/@12.9245901,77.4134618,12z/data=!4m10!1m2!2m1!1sAMS+Scrap+Mercant+neaar+jp+nagar!3m6!1s0x3bae1596b577ac9b:0x4e93e3352888d4b2!8m2!3d12.9245901!4d77.5658971!15sCiBBTVMgU2NyYXAgTWVyY2FudCBuZWFhciBqcCBuYWdhcloiIiBhbXMgc2NyYXAgbWVyY2FudCBuZWFhciBqcCBuYWdhcpIBEnNjcmFwX21ldGFsX2RlYWxlcuABAA!16s%2Fg%2F11npck2dpr?entry=ttu&g_ep=EgoyMDI2MTAyOC4wIKXMDSoASAFQAw%3D%3D",  # Paste Ramesh Traders' Google Maps share link here.
        },
        {
            "name": "AT Scrap",
            "lat": 12.9230, "lon": 77.5530,
            "share": 0.95,
            "speciality": "Metals, Copper, Steel, Aluminium, E-Waste",
            "phone": "+916363640159",  # Enter Shree Metals' individual WhatsApp number.
            "maps_url": "https://www.google.com/maps/place/AT+Scrap+-+Metal,+Copper,+Aluminium+and+Electronic+Items/@12.9245643,77.4134616,12z/data=!4m10!1m2!2m1!1sscrap+shop+near+banashakari!3m6!1s0x3bae151f6301d38f:0xaff5d348a20e5ba6!8m2!3d12.9154428!4d77.5726013!15sChxzY3JhcCBzaG9wIG5lYXIgYmFuYXNoYW5rYXJpWh4iHHNjcmFwIHNob3AgbmVhciBiYW5hc2hhbmthcmmSAQxzYWx2YWdlX3lhcmTgAQA!16s%2Fg%2F11w2k6yz06?entry=ttu&g_ep=EgoyMDI2MTAyOC4wIKXMDSoASAFQAw%3D%3D",  # Paste Shree Metals' Google Maps share link here.
        },
        {
            "name": "S A Scrap Traders",
            "lat": 12.9010, "lon": 77.5900,
            "share": 0.82,
            "speciality": "All household scrap",
            "phone": "+919880459102",  # Enter Lakshmi Scrap Centre's individual number.
            "maps_url": "https://www.google.com/maps/place/S+A+Traders.all+types+of+Old+scrap+%26+services+all+electrical+items+%26+extra.../@12.9245643,77.4134616,12z/data=!4m10!1m2!2m1!1sscrap+shop+near+banashakari!3m6!1s0x3bae1565aeb78ebb:0xc0db74270e02c275!8m2!3d12.9094906!4d77.5701551!15sChxzY3JhcCBzaG9wIG5lYXIgYmFuYXNoYW5rYXJpWh4iHHNjcmFwIHNob3AgbmVhciBiYW5hc2hhbmthcmmSARJzY3JhcF9tZXRhbF9kZWFsZXKaASRDaGREU1VoTk1HOW5TMFZKUTBGblNVUlllbDlYWm10blJSQULgAQD6AQQIABA1!16s%2Fg%2F1yfjqffm9?entry=ttu&g_ep=EgoyMDI2MTAyOC4wIKXMDSoASAFQAw%3D%3D",  # Paste Lakshmi Scrap Centre's Google Maps share link here.
        },
        {
            "name": "SKF Scrap Dealers",
            "lat": 12.9300, "lon": 77.5800,
            "share": 0.91,
            "speciality": "Paper, cardboard, glass",
            "phone": "+919731477886",  
            "maps_url": "https://www.google.com/maps/place/Skf+Traders+-+Scrap+Dealers/@12.9245643,77.4134616,12z/data=!4m10!1m2!2m1!1sscrap+shop+near+banashakari!3m6!1s0x3bae3fa71da1a823:0xdb1660f2b5769cc8!8m2!3d12.9238371!4d77.5475943!15sChxzY3JhcCBzaG9wIG5lYXIgYmFuYXNoYW5rYXJpWh4iHHNjcmFwIHNob3AgbmVhciBiYW5hc2hhbmthcmmSAQxzYWx2YWdlX3lhcmSaAURDaTlEUVVsUlFVTnZaRU5vZEhsalJqbHZUMnhuZWsxc1pHWmtWMVpPWVVob2NFMXVhR0ZWUnpWRFYwVldlbU5XUlJBQuABAPoBBAgAEBg!16s%2Fg%2F11z5wl311p?entry=ttu&g_ep=EgoyMDI2MTAyOC4wIKXMDSoASAFQAw%3D%3D",  # Paste Basavanna Recyclers' Google Maps share link here.
        },
    ],

    
    "JP Nagar": [
        {
            "name": "Scrapxperts - Metal Scrap Buyers",
            "lat": 12.9140, "lon": 77.5600,
            "share": 0.88,
            "speciality": "Metal, Steel, Copper",
            "phone": "+919008180928",  # Enter Ramesh Traders' individual WhatsApp number.
            "maps_url": "https://www.google.com/maps/place/Scrapxperts+-+Metal+Scrap+Buyers+%7C+Office+%26+Home+Clearance+Services/@12.8727812,77.5287366,14z/data=!4m10!1m2!2m1!1sscrap+shop+near+jp+nagar!3m6!1s0x3bae6bd34adc8ca5:0xa167b05e57b64612!8m2!3d12.8727812!4d77.5668454!15sChhzY3JhcCBzaG9wIG5lYXIganAgbmFnYXJaGiIYc2NyYXAgc2hvcCBuZWFyIGpwIG5hZ2FykgESc2NyYXBfbWV0YWxfZGVhbGVy4AEA!16s%2Fg%2F11nhxhpbg3?entry=ttu&g_ep=EgoyMDI2MTAyOC4wIKXMDSoASAFQAw%3D%3D",  # Paste AMS Scrap Mercant's Google Maps share link here.
        },
        {
            "name": "Twin Leafs Enterprises",
            "lat": 12.9230, "lon": 77.5530,
            "share": 0.94,
            "speciality": "All household scrap",
            "phone": "+919880382650",  # Enter Shree Metals' individual WhatsApp number.
            "maps_url": "https://www.google.com/maps/place/Twin+Leafs+Enterprises/@12.9201365,77.5522616,14z/data=!4m10!1m2!2m1!1sscrap+shop+near+jp+nagar!3m6!1s0x3bae15f13b440193:0x7575c7c205996e22!8m2!3d12.9088969!4d77.5735073!15sChhzY3JhcCBzaG9wIG5lYXIganAgbmFnYXJaGiIYc2NyYXAgc2hvcCBuZWFyIGpwIG5hZ2FykgESc2NyYXBfbWV0YWxfZGVhbGVymgEkQ2hkRFNVaE5NRzluUzBWSlEwRm5TVVJhTjB4VE1qSlJSUkFC4AEA-gEECE8QMA!16s%2Fg%2F11ff526995?entry=ttu&g_ep=EgoyMDI2MTAyOC4wIKXMDSoASAFQAw%3D%3D",  # Paste AT Scrap's Google Maps share link here.
        },
    ],

    
    "Jayanagar": [
         {
            "name": "JB Steel Scrap Dealers",
            "lat": 12.9140, "lon": 77.5600,
            "share": 0.88,
            "speciality": "Metal, Steel, Copper",
            "phone": "+919945909873",  # Enter Ramesh Traders' individual WhatsApp number.
            "maps_url": "https://www.google.com/maps/place/JB+Steel+Scrap+Dealers/@12.9201365,77.5522616,14z/data=!4m10!1m2!2m1!1sscrap+shop+near+jp+nagar!3m6!1s0x3bae15799837be91:0x83a2372cfab99e5c!8m2!3d12.9201413!4d77.5903705!15sChhzY3JhcCBzaG9wIG5lYXIganAgbmFnYXJaGiIYc2NyYXAgc2hvcCBuZWFyIGpwIG5hZ2FykgESc2NyYXBfbWV0YWxfZGVhbGVy4AEA!16s%2Fg%2F11ghls98fx?entry=ttu&g_ep=EgoyMDI2MTAyOC4wIKXMDSoASAFQAw%3D%3D",  # Paste AMS Scrap Mercant's Google Maps share link here.
        },
    ],
}

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
    rows = (db_client().table("user_profiles").select("user_id, full_name, email, phone")
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


def analyze_photos(photos, picks, force=False):
    """Analyses every photo at the same time. Returns (results, errors). Repeat photos come from a cache."""
    key, model = get_api_key(), get_model()
    cache = st.session_state.setdefault("detect_cache", {})
    results = [None] * len(photos)
    todo = []
    for i, (name, data) in enumerate(photos):
        h = hashlib.sha1(data).hexdigest() + model
        if h in cache and not force:
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
    picks = picks or []
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


def decode_audio(raw: bytes, filename=""):
    """Decode WAV directly and use PyAV to convert MP3 and other common uploads to mono WAV samples."""
    if filename.lower().endswith(".wav"):
        return read_wav(raw)

    import av

    sample_rate = 22050
    samples = []
    with av.open(io.BytesIO(raw)) as container:
        audio_stream = next((stream for stream in container.streams if stream.type == "audio"), None)
        if audio_stream is None:
            raise ValueError("No audio stream found")
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=sample_rate)
        for frame in container.decode(audio_stream):
            for converted in resampler.resample(frame):
                samples.append(converted.to_ndarray().astype(np.float32).reshape(-1))
        for converted in resampler.resample(None):
            samples.append(converted.to_ndarray().astype(np.float32).reshape(-1))
    if not samples:
        raise ValueError("No audio samples found")
    return sample_rate, np.concatenate(samples)[: sample_rate * 12]


def analyze_audio(raw: bytes, filename=""):
    """
    Prototype heuristic standing in for a fine-tuned YAMNet classifier.
    It uses general audio signals (loudness stability, noise, and high-frequency rattle),
    rather than a separate trained classifier for each appliance type.
    """
    sr, x = decode_audio(raw, filename)
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


def whatsapp_url(phone, message):
    """Opens a WhatsApp chat with the message already typed in. wa.me needs digits only, no + sign."""
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    return f"https://wa.me/{digits}?text={quote(message, safe='')}"


def pickup_message(dealer, user_name, area, items, kg, fair):
    """Personalised pickup request: who is asking, where, what scrap, how much, and ReLoop's fair value."""
    parts = [f"{m} {w:.1f} kg" for m, w in items if w > 0]
    lines = [f"Hello {dealer}, this is {user_name or 'a ReLoop user'} from {area}."]
    if kg > 0 and parts:
        lines.append(f"I have about {kg:.1f} kg of scrap ready to sell: {', '.join(parts)}.")
    else:
        lines.append("I have some scrap ready to sell.")
    if fair > 0:
        lines.append(f"ReLoop estimates its fair value at ₹{fair * 0.9:,.0f} to ₹{fair * 1.1:,.0f}.")
    lines.append("Could you please come for a pickup? Let me know a time that suits you.")
    lines.append("Sent via ReLoop")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# Hero (shown at the top of the Scrap Vision tab, under the main navigation)
# ----------------------------------------------------------------------------
board_rows = "".join(
    f'<div class="rl-row"><span>{m}</span><b>₹{r}/kg</b></div>'
    for m, r in [("Newspaper / paper", 12), ("Iron", 28), ("Aluminium", 105), ("Copper", 650)]
)
ticker = "".join(f'<span>{m}<b>₹{r}/kg</b></span>' for m, r in RATES.items())
if active_section == "Scrap Vision":
    st.markdown(
    f"""
<div class="rl-hero">
  <div>
    <span class="rl-pill">REUSE · RECYCLE · RESELL</span>
    <h1 class="rl-display">Know what it's worth before you sell it.</h1>
    <p>Reuse it, Resell it, or Recycle it. ReLoop helps you decide !</p>
    <div class="rl-chips">
      <span class="rl-chip">Estimate <b>Scrap</b> prices by photo</span>
      <span class="rl-chip">Pass Used <b>Books</b> to Juniors</span>
      <span class="rl-chip">Judge the condition of <b>Appliances</b> by sound</span>
    </div>
  </div>
  <div class="rl-board">
    <h4><span class="rl-dot"></span>Today's SCRAP Rates</h4>
    {board_rows}
  </div>
</div>
<div class="rl-ticker"><div class="rl-track">{ticker}{ticker}</div></div>
<div class="rl-flow">
  <div><span class="rl-step">01 · START</span><b>Capture</b><span>Photo, Sound or Text</span></div>
  <div><span class="rl-step">02 · UNDERSTAND</span><b>Analyse</b><span>Analyse using AI</span></div>
  <div><span class="rl-step">03 · CHOOSE</span><b>Decide</b><span>Reuse, Resell or Recycle</span></div>
  <div><span class="rl-step">04 · COMPLETE</span><b>Connect</b><span>Local Scrap Collector or Buyer</span></div>
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
        listButton.style.setProperty("--list-glow-alpha", (0.20 + bt * 0.54).toFixed(3));
        listButton.style.setProperty("--list-glow-blur", (12 + bt * 54).toFixed(0) + "px");
        listButton.style.setProperty("--list-glow-spread", (bt * 4).toFixed(0) + "px");
        listButton.style.setProperty("--list-shadow-alpha", (0.14 + bt * 0.34).toFixed(3));
        listButton.style.setProperty("--list-shadow-blur", (24 + bt * 76).toFixed(0) + "px");
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
if active_section == "Scrap Vision":
    st.subheader("What is your scrap worth ?")
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
            st.caption(f"{len(photos)} photo{'s' if len(photos) != 1 else ''} in the pile. You can add more items to the pile.")
            reverify = st.button("↻ ReVerify", key="reverify_photos")
            # Analyze automatically once when the uploaded/captured photo set changes.
            # Its signature prevents API calls from repeating on normal Streamlit reruns.
            photo_signature = tuple(
                (name, hashlib.sha1(data).hexdigest()) for name, data in photos
            )
            if reverify or photo_signature != st.session_state.get("scrap_photo_signature"):
                st.session_state.scrap_photo_signature = photo_signature
                st.session_state.scrap_ver += 1
                if has_api_key():
                    with st.spinner(f"Reading {len(photos)} photo{'s' if len(photos) != 1 else ''}..."):
                        t0 = time.perf_counter()
                        results, errors = analyze_photos(photos, [], force=reverify)
                        elapsed = time.perf_counter() - t0
                    st.session_state.photo_results = [
                        dict(name=n, df=r[0], tm=r[1]) for (n, _), r in zip(photos, results)
                    ]
                    st.session_state.scrap_df = (
                        pd.concat([r[0] for r in results])
                        .groupby("Material", as_index=False, sort=False)["Weight (kg)"].sum()
                    )
                    
                    if errors:
                        st.warning("Some photos could not be analysed. Add their materials and weights in the table.")
                        st.error("Gemini error: " + " || ".join(errors)[:700])
                        st.info(key_status())
                else:
                    st.session_state.photo_results = []
                    st.info("Photo uploaded. Add GEMINI_API_KEY to Streamlit secrets to analyze it automatically, or enter materials and weights in the table.")
        else:
            st.session_state.scrap_photo_signature = None
            st.caption("No photo yet? You can still enter materials by hand on the right.")

    with right:
        if st.session_state.scrap_df is None:
            st.session_state.scrap_df = pd.DataFrame({
                "Material": pd.Series(dtype="object"),
                "Weight (kg)": pd.Series(dtype="float64"),
            })
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
        # The selector is shown below the valuation, but its saved value is used here.
        area = st.session_state.get("scrap_dealer_area", next(iter(AREAS)))
        area_dealers = DEALERS_BY_AREA.get(area, [])
        best_dealer = max(area_dealers, key=lambda dealer: dealer["share"], default=None)
        best_offer = fair * best_dealer["share"] if best_dealer else 0.0

        st.markdown(
            f'<div class="rl-value"><div class="rl-meta" style="color:{SPRUCE};font-weight:500">Fair value of {kg:.1f} kg, all photos</div>'
            f'<div class="n">₹{fair * 0.9:,.0f} to ₹{fair * 1.1:,.0f}</div></div>',
            unsafe_allow_html=True,
        )
        if st.button(
            "ADD TO IMPACT",
            key="save_scrap",
            disabled=(fair <= 0 or best_dealer is None),
            use_container_width=True,
        ):
            try:
                add_impact_record(
                    "scrap",
                    kg=kg,
                    amount=best_offer,
                    details={
                        "materials": df.to_dict(orient="records"),
                        "area": area,
                        "best_offer_dealer": best_dealer["name"],
                        "best_offer_amount": round(best_offer, 2),
                        "estimated_fair_value": round(fair, 2),
                    },
                )
                st.toast(f"Saved ₹{best_offer:,.0f} from {best_dealer['name']} to your impact.")
            except Exception as e:
                st.error(f"Could not save impact data. Run the Supabase setup SQL first. ({type(e).__name__})")
        if best_dealer:
            pass
        else:
            st.caption(f"Add kabadiwalas for {area} in DEALERS_BY_AREA to save a shop offer to your impact.")

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
            st.caption("Values are AI estimated. You can edit the values if needed.")

    st.markdown("&nbsp;")
    st.subheader("Who should you sell to?")
    area = st.selectbox(
        "Choose your area", list(AREAS), key="scrap_dealer_area",
        index=list(AREAS).index(area),
    )
    me = AREAS[area]
    area_dealers = DEALERS_BY_AREA.get(area, [])
    rows = []
    # Get the owner-maintained dealer list for only the selected neighborhood.
    for dealer in area_dealers:
        rows.append({
            "name": dealer["name"],
            "km": haversine_km(me, (dealer["lat"], dealer["lon"])),
            "offer": fair * dealer["share"],
            "spec": dealer["speciality"],
            "share": dealer["share"],
            "phone": dealer["phone"],
            # An owner-provided Maps share URL takes precedence; coordinates are the fallback.
            "maps_url": dealer.get("maps_url", "").strip()
            or f"https://www.google.com/maps/search/?api=1&query={dealer['lat']},{dealer['lon']}",
        })
    rows.sort(key=lambda r: -r["offer"])

    user_name = (current_user.get("name") or (current_user.get("metadata") or {}).get("full_name") or "").strip()
    scrap_items = [(m, float(w)) for m, w in zip(df["Material"], df["Weight (kg)"])]
    dealer_cards = []
    for i, r in enumerate(rows):
        best = '<span class="rl-tag best">Best offer</span>' if i == 0 else ""
        if r["phone"].strip():
            wa_link = whatsapp_url(
                r["phone"], pickup_message(r["name"], user_name, area, scrap_items, kg, fair)
            )
            contact_action = (
                f'<a class="rl-wa-btn" href="{html.escape(wa_link, quote=True)}" '
                'target="_blank" rel="noopener noreferrer">Contact for pickup</a>'
            )
        else:
            contact_action = '<span class="rl-wa-btn" style="opacity:.65;cursor:default">Contact number not set</span>'
        map_url = html.escape(r["maps_url"], quote=True)
        location_icon = (
            '<a class="rl-location-link" '
            f'href="{map_url}" target="_blank" rel="noopener noreferrer" '
            f'aria-label="Open {html.escape(r["name"], quote=True)} in Google Maps" '
            f'title="Open {html.escape(r["name"], quote=True)} in Google Maps">'
            '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true" '
            'xmlns="http://www.w3.org/2000/svg">'
            '<path d="M20 10.2c0 5.1-8 11.3-8 11.3S4 15.3 4 10.2a8 8 0 1 1 16 0Z" '
            'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
            '<circle cx="12" cy="10" r="2.6" stroke="currentColor" stroke-width="1.8"/>'
            '</svg></a>'
        )
        dealer_cards.append(
            '<div class="rl-dealer">'
            f'<h4>{html.escape(r["name"])}</h4>'
            '<div class="rl-dealer-price-row">'
            f'<div class="rl-price">₹{r["offer"]:,.0f}</div>{location_icon}'
            '</div>'
            f'<div class="rl-meta">{r["share"] * 100:.0f}% of fair value</div>'
            f'<div class="rl-bar"><i style="width:{r["share"] * 100:.0f}%"></i></div>'
            f'<div class="rl-dealer-tags"><span class="rl-tag">{html.escape(r["spec"])}</span>{best}</div>'
            f'{contact_action}'
            '</div>'
        )
    st.markdown('<div class="rl-dealer-grid">' + "".join(dealer_cards) + '</div>', unsafe_allow_html=True)
    if not rows:
        st.info(f"No kabadiwalas are listed for {area} yet.")
    elif any(not dealer["phone"].strip() for dealer in rows):
        st.caption("Dealer names, locations, offers, and each dealer's own WhatsApp number are managed by the owner in DEALERS_BY_AREA near the top of app.py.")
# ----------------------------------------------------------------------------
# Book Loop
# ----------------------------------------------------------------------------
if active_section == "Book Loop":
    title_col, action_col = st.columns([4.5, 1.8], vertical_alignment="center")
    title_col.subheader("Pass on your books. Buy for less.")
    if action_col.button("＋ List a book", key="list_book_button", use_container_width=True):
        st.session_state.show_book_form = True

    f1, f2, f3 = st.columns([1, 1, 2])
    book_exams = [
        "JEE", "NEET", "Boards", *[f"Class {grade}" for grade in range(1, 13)],
        "Civil Services", "Other",
    ]
    book_subjects = [
        "Physics", "Chemistry", "Maths", "Biology", "English", "Social Studies",
        "Computer Science", "General Knowledge", "Other",
    ]
    exam = f1.selectbox("Exam / category", ["All", *book_exams])
    subject = f2.selectbox("Subject", ["All", *book_subjects])
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

    # The marketplace grid is for books you can browse and request from others.
    # Your own active and sold listings are rendered in the separate section below.
    shown = [
        row for row in listings
        if str(row.get("owner_id")) != str(current_user["id"])
        if (exam == "All" or row["exam"] == exam)
        and (subject == "All" or row["subject"] == subject)
        and q.lower() in row["title"].lower()
    ]
    if not shown:
        st.info("No books from other sellers match these filters. Your books for sale are shown separately below.")

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
                    st.button("Request sent", key=f"req_{listing_id}", disabled=True, use_container_width=True)
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
                elif st.button("Request this book", key=f"req_{listing_id}", use_container_width=True):
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
                title = st.text_input("Book title")
                c1, c2, c3, c4 = st.columns(4)
                ex = c1.selectbox("Exam / category", book_exams)
                sub = c2.selectbox("Subject", book_subjects)
                cond = c3.selectbox("Condition", ["Like new", "Good", "Fair"])
                mrp = c4.number_input("Original price (₹)", min_value=50, value=500, step=10)
                price = st.slider("Your price (₹)", 10, int(mrp), int(mrp * 0.4))
                publish = st.form_submit_button("Publish listing")
            if publish:
                seller_name = (
                    current_user.get("name")
                    or (current_user.get("metadata") or {}).get("full_name")
                    or str(current_user.get("email") or "").split("@")[0]
                ).strip()
                if not title.strip():
                    st.error("Add the book title.")
                elif not seller_name:
                    st.error("Add your name under Account before listing a book.")
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
                            "mrp": int(mrp), "price": int(price), "seller_name": seller_name,
                        }).execute()
                        st.session_state.show_book_form = False
                        st.success("Book listing saved to ReLoop.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Could not save the listing. Check Supabase SQL. ({type(e).__name__})")

    # Keep the seller's inventory visually separate and after the buyable listings.
    st.divider()
    inventory_title, refresh_col = st.columns([4.5, 1.5], vertical_alignment="center")
    inventory_title.markdown("### Your books for sale")
    if refresh_col.button("↻ Check requests", key="refresh_my_book_requests", use_container_width=True):
        st.rerun()
    st.caption("Your books are listed here !")
    try:
        my_listings = load_my_book_listings()
        my_requests = load_requests_for_listings([row["id"] for row in my_listings])
    except Exception as e:
        my_listings, my_requests = [], []
        st.error(f"Could not load your listings and requests. ({type(e).__name__})")
    counts = {}
    requests_by_listing = {}
    for request in my_requests:
        key = str(request["listing_id"])
        counts[key] = counts.get(key, 0) + 1
        requests_by_listing.setdefault(key, []).append(request)
    requester_ids = {
        str(request["requester_id"])
        for request in my_requests if request.get("requester_id")
    }
    try:
        requester_profiles = load_contact_profiles(requester_ids)
    except Exception:
        requester_profiles = {}
    active_mine = [book for book in my_listings if book.get("status", "active") == "active"]

    if not active_mine:
        st.caption("You have no active book listings.")
    for row_start in range(0, len(active_mine), 3):
        mine_cols = st.columns(3, gap="medium")
        for seller_col, book in zip(mine_cols, active_mine[row_start:row_start + 3]):
            with seller_col:
                listing_id = str(book["id"])
                listing_requests = requests_by_listing.get(listing_id, [])
                request_count = counts.get(listing_id, 0)
                saving = int(book["mrp"]) - int(book["price"])
                accent = {"Physics": MARIGOLD, "Chemistry": MINT, "Maths": CORAL, "Biology": "#9DB7FF"}.get(book["subject"], MARIGOLD)
                st.markdown(
                    f"""<div class="rl-card" style="border-top:3px solid {accent}">
                    <h4>{html.escape(book['title'])}</h4>
                    <div class="rl-meta">Listed by {html.escape(book['seller_name'])}</div>
                    <div style="margin:10px 0 2px 0"><span class="rl-price">₹{int(book['price'])}</span>
                    <span class="rl-strike">₹{int(book['mrp'])}</span></div>
                    <div class="rl-meta">You save ₹{saving}</div>
                    <span class="rl-tag">{html.escape(book['exam'])}</span>
                    <span class="rl-tag">{html.escape(book['subject'])}</span>
                    <span class="rl-tag">{html.escape(book['condition'])}</span>
                    <div class="rl-meta" style="margin-top:12px">Your listing · {request_count} buyer request{'s' if request_count != 1 else ''}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )
                if listing_requests:
                    with st.expander(f"View buyer requests · {len(listing_requests)}"):
                        for request_index, request in enumerate(listing_requests, start=1):
                            requester = requester_profiles.get(str(request.get("requester_id")), {})
                            st.markdown(f"**Request {request_index}**")
                            st.write(f"Name: {requester.get('full_name') or 'ReLoop member'}")
                            st.write(f"Email: {requester.get('email') or 'Not available'}")
                            st.write(f"Phone: {requester.get('phone') or 'Not available'}")
                            if request_index < len(listing_requests):
                                st.divider()
                if st.button("Sold", key=f"sold_{listing_id}", use_container_width=True):
                    try:
                        db_client().rpc("mark_book_sold", {"p_listing_id": int(book["id"])}).execute()
                        st.success("Marked sold. It’s removed from active listings and you earned 100 ReLoop points.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Could not mark the book sold. Run the latest sales migration SQL. ({type(e).__name__})")

# ----------------------------------------------------------------------------
# Sound Check
# ----------------------------------------------------------------------------
if active_section == "Sound Check":
    st.subheader("Does it still run well?")
    st.write("Record a steady 5 to 10 second sound sample while the appliance is running.")
    res = None
    s1, s2 = st.columns([1, 1.2], gap="large")
    with s1:
        appliance_choice = st.selectbox(
            "Appliance",
            [
                "Ceiling or table fan", "Mixer grinder", "Washing machine", "Refrigerator",
                "Water pump", "Vacuum cleaner", "Air cooler", "Electric motor", "Other",
            ],
        )
        if appliance_choice == "Other":
            other_appliance = st.text_input("Appliance name", placeholder="e.g. exhaust fan")
            appliance = other_appliance.strip() or "Other appliance"
        else:
            appliance = appliance_choice
        st.caption("Record the appliance running steadily in a quiet place.")
        rec = st.audio_input("Record the sound")
        up = st.file_uploader("or upload a WAV or MP3 file", type=["wav", "mp3"])
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
                res = analyze_audio(audio.getvalue(), getattr(audio, "name", ""))
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
                st.error("Could not read this audio. Upload a valid WAV file or add the PyAV package to requirements.txt to enable MP3 decoding.")

    if res is not None and not res["silent"]:
        step = max(1, len(res["x"]) // 1500)
        st.caption("Waveform")
        st.line_chart(pd.DataFrame({"amplitude": res["x"][::step]}), height=160, color=MARIGOLD)
    

# ----------------------------------------------------------------------------
# Impact
# ----------------------------------------------------------------------------
LEVELS = [(0, "Seedling", "🌱"), (100, "Sprout", "🌿"),
          (1000, "Champion", "🏆"), (3000, "Legend", "🌍")]
VERDICT_LABELS = {"RESELL": ("Resell", MINT), "REPAIR": ("Repair", MARIGOLD), "SCRAP": ("Scrap", CORAL)}
BAR_COLOURS = [MINT, MARIGOLD, CORAL, "#9DB7FF", "#C7A6FF", "#7FD1E8"]


def imp_money(x):
    return f"₹{x:,.0f}"


def imp_level(points):
    idx = max(i for i, (need, _, _) in enumerate(LEVELS) if points >= need)
    cur = LEVELS[idx]
    nxt = LEVELS[idx + 1] if idx + 1 < len(LEVELS) else None
    pct = 100.0 if nxt is None else (points - cur[0]) / (nxt[0] - cur[0]) * 100
    return cur, nxt, pct


def imp_stat(label, value):
    return f'<div class="rl-imp-stat"><span>{html.escape(label)}</span><b>{value}</b></div>'


def imp_bar(label, value_text, pct, colour):
    width = 0 if pct <= 0 else max(pct, 3)
    return (
        '<div class="rl-imp-barrow"><div class="rl-imp-barlabel">'
        f'<span><i class="d" style="background:{colour}"></i>{html.escape(label)}</span><em>{value_text}</em></div>'
        f'<div class="rl-imp-track"><i style="width:{width:.0f}%;background:{colour}"></i></div></div>'
    )


def imp_panel(icon, title, subtitle, accent, body):
    return (
        f'<div class="rl-imp-panel" style="--accent:{accent}">'
        f'<div class="rl-imp-panel-head"><div class="rl-imp-icon" style="background:{accent}22">{icon}</div>'
        f'<div><h4>{html.escape(title)}</h4><small>{html.escape(subtitle)}</small></div></div>'
        f'{body}</div>'
    )


def imp_when(ts):
    try:
        return pd.to_datetime(ts, utc=True).tz_convert("Asia/Kolkata").strftime("%d %b · %I:%M %p")
    except Exception:
        return ""


def imp_feed_item(record):
    kind = record.get("kind")
    d = record.get("details") or {}
    amount = float(record.get("amount") or 0)
    if kind == "scrap":
        names = [str(m.get("Material")) for m in (d.get("materials") or []) if isinstance(m, dict) and m.get("Material")]
        icon, colour = reloop_logo_mark(24), MINT
        title = f"Valued {float(record.get('kg') or 0):.1f} kg of scrap"
        sub = (", ".join(names[:3]) + ("…" if len(names) > 3 else "")) if names else "Scrap valuation"
        right, rcol = imp_money(amount), MARIGOLD
    elif kind == "book_request":
        icon, colour = "📚", "#9DB7FF"
        title, sub = "Requested a book", "Request sent to the owner"
        right, rcol = f"{imp_money(amount)} saved", MINT
    elif kind == "book_sold":
        icon, colour = "🏷️", MARIGOLD
        title, sub = "Sold a book", f"+{int(d.get('points', 0))} ReLoop points"
        right, rcol = imp_money(amount), MARIGOLD
    elif kind == "appliance":
        label, vcol = VERDICT_LABELS.get(d.get("verdict"), (str(d.get("verdict") or "Checked"), MUTE))
        icon, colour = "🔊", CORAL
        title = f"Checked a {str(d.get('appliance') or 'appliance').lower()}"
        sub = "Sound check"
        right, rcol = label.upper(), vcol
    else:
        icon, colour, title, sub, right, rcol = "•", MUTE, str(kind or "Activity"), "", "", MUTE
    when = imp_when(record.get("created_at"))
    sub_full = f"{sub} · {when}" if (sub and when) else (sub or when)
    return (
        '<div class="rl-imp-item">'
        f'<div class="rl-imp-dot" style="background:{colour}22">{icon}</div>'
        f'<div class="t"><b>{html.escape(title)}</b><span>{html.escape(sub_full)}</span></div>'
        f'<div class="rl-imp-amt" style="color:{rcol}">{html.escape(right)}</div></div>'
    )


if active_section == "Impact":
    st.subheader("What you have kept in use")
    st.caption("Everything you have valued, requested, sold and checked, saved to your ReLoop account.")

    try:
        records = load_impact_records()
    except Exception as e:
        records = []
        st.error(f"Could not load your impact history. Run the Supabase setup SQL and check RLS policies. ({type(e).__name__})")

    scrap_rows = [r for r in records if r["kind"] == "scrap"]
    book_rows = [r for r in records if r["kind"] == "book_request"]
    audio_rows = [r for r in records if r["kind"] == "appliance"]
    sale_rows = [r for r in records if r["kind"] == "book_sold"]

    kg_total = sum(float(r.get("kg") or 0) for r in scrap_rows)
    value_total = sum(float(r.get("amount") or 0) for r in scrap_rows)
    saved_total = sum(float(r.get("amount") or 0) for r in book_rows)
    sale_value_total = sum(float(r.get("amount") or 0) for r in sale_rows)
    points_total = sum(int((r.get("details") or {}).get("points", 0)) for r in sale_rows)
    total_unlocked = value_total + saved_total + sale_value_total

    verdict_counts = {name: 0 for name in ("RESELL", "REPAIR", "SCRAP")}
    for record in audio_rows:
        verdict = (record.get("details") or {}).get("verdict")
        if verdict in verdict_counts:
            verdict_counts[verdict] += 1
    verdict_total = sum(verdict_counts.values())

    material_kg = {}
    for record in scrap_rows:
        for m in (record.get("details") or {}).get("materials") or []:
            if isinstance(m, dict) and m.get("Material") and m.get("Weight (kg)") is not None:
                material_kg[m["Material"]] = material_kg.get(m["Material"], 0.0) + float(m["Weight (kg)"])

    # ---- Row 1: headline value + points level ----
    cur, nxt, level_pct = imp_level(points_total)
    next_text = "Top level reached. Thank you for keeping things in use." if nxt is None \
        else f"{nxt[0] - points_total:,} points to {nxt[1]}"
    benchmark_html = "".join(
        f'<span class="rl-imp-benchmark{" current" if level_name == cur[1] else ""}">'
        f'<b>{html.escape(level_name)}</b><small>{threshold:,}</small></span>'
        for threshold, level_name, emoji in LEVELS
    )
    hero_html = (
        '<div class="rl-imp-hero">'
        '<div class="rl-imp-eyebrow">Value unlocked</div>'
        f'<div class="rl-imp-big">{imp_money(total_unlocked)}</div>'
        '<div class="rl-imp-sub">Scrap you have valued, books passed on to juniors and books you have sold through ReLoop.</div>'
        '<div class="rl-imp-split">'
        f'<span><b>{imp_money(value_total)}</b>scrap value</span>'
        f'<span><b>{imp_money(saved_total)}</b>saved by juniors</span>'
        f'<span><b>{imp_money(sale_value_total)}</b>from book sales</span>'
        '</div></div>'
    )
    level_html = (
        '<div class="rl-imp-level">'
        '<div class="rl-imp-eyebrow">ReLoop points</div>'
        '<div class="rl-imp-levelrow">'
        f'<div class="rl-imp-badge">{cur[2]}</div>'
        f'<div><div class="rl-imp-pts">{points_total:,}</div><div class="rl-imp-lvl">{cur[1]} level</div></div></div>'
        f'<div class="rl-imp-progress"><i style="width:{level_pct:.0f}%"></i></div>'
        f'<div class="rl-imp-next">{next_text}</div>'
        f'<div class="rl-imp-contrib"><b>Your progress:</b> {len(sale_rows)} books sold · {points_total:,} points · 100 points per sale</div>'
        f'<div class="rl-imp-benchmarks">{benchmark_html}</div>'
        '</div>'
    )
    st.markdown(f'<div class="rl-imp-grid g-head">{hero_html}{level_html}</div>', unsafe_allow_html=True)

    # ---- Row 2: scrap, books, appliances ----
    scrap_body = (
        imp_stat("Weight valued", f"{kg_total:.1f} kg")
        + imp_stat("Estimated value", imp_money(value_total))
        + imp_stat("Valuations saved", f"{len(scrap_rows)}")
    )
    books_body = (
        imp_stat("Books requested", f"{len(book_rows)}")
        + imp_stat("Saved by juniors", imp_money(saved_total))
        + imp_stat("Books sold", f"{len(sale_rows)}")
        + imp_stat("Sale value", imp_money(sale_value_total))
    )
    verdict_body = ""
    for v in ("RESELL", "REPAIR", "SCRAP"):
        share = verdict_counts[v] / verdict_total * 100 if verdict_total else 0
        text = f"{verdict_counts[v]} · {share:.0f}%" if verdict_total else "0"
        verdict_body += imp_bar(VERDICT_LABELS[v][0], text, share, VERDICT_LABELS[v][1])
    if not verdict_total:
        verdict_body += '<div class="rl-imp-empty">Record any listed appliance on the Sound Check tab and save its verdict to see it here.</div>'

    st.markdown(
        '<div class="rl-imp-grid g-three">'
        + imp_panel(reloop_logo_mark(26), "Scrap", "Photo valuations", MINT, scrap_body)
        + imp_panel("📚", "Books", "Book Loop activity", "#9DB7FF", books_body)
        + imp_panel("🔊", "Appliance verdicts", f"{verdict_total} checked", CORAL, verdict_body)
        + '</div>',
        unsafe_allow_html=True,
    )

    # ---- Row 3: what you valued + recent activity ----
    top_materials = sorted(material_kg.items(), key=lambda kv: -kv[1])[:6]
    if top_materials:
        biggest = top_materials[0][1] or 1.0
        mat_body = "".join(
            imp_bar(name, f"{kg:.1f} kg", kg / biggest * 100, BAR_COLOURS[i % len(BAR_COLOURS)])
            for i, (name, kg) in enumerate(top_materials)
        )
    else:
        mat_body = '<div class="rl-imp-empty">Value some scrap on the Scrap Vision tab and save it to see which materials you keep in use.</div>'

    recent = sorted(records, key=lambda r: str(r.get("created_at") or ""), reverse=True)[:6]
    if recent:
        feed_body = "".join(imp_feed_item(r) for r in recent)
    else:
        feed_body = '<div class="rl-imp-empty">Nothing here yet. Your scrap valuations, book requests, sales and sound checks will appear as you use ReLoop.</div>'

    st.markdown(
        '<div class="rl-imp-grid g-detail">'
        + imp_panel("⚖️", "Scrap by material", "Total weight you have valued", MARIGOLD, mat_body)
        + imp_panel("🕒", "Recent activity", "Your latest actions", MINT, feed_body)
        + '</div>',
        unsafe_allow_html=True,
    )
