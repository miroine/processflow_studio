"""Visual theme: Equinor-inspired colours (EDS moss green / energy red / slate), header and credit footer.

Only colours and typography are borrowed; no Equinor logo or trademark artwork is used, and the app
states that it is an independent educational tool.
"""
from __future__ import annotations

import streamlit as st

AUTHOR = "Merouane Hamdani"
APP = "ProcessFlow Studio"
DISCLAIMER = ("For educational purposes only. Independent tool — not an official product of, affiliated with, or "
              "endorsed by Equinor or AspenTech. Results are screening-level; verify with qualified software and "
              "engineering judgement before any use.")

FOOTER_NOTE = ("For educational purposes only · independent tool, not affiliated with or endorsed by Equinor or "
               "AspenTech · screening-level results")

MOSS, MOSS_DARK, MOSS_LIGHT = "#007079", "#004F55", "#DEEDEE"
ENERGY_RED, SLATE, TEXT, BG2 = "#FF1243", "#243746", "#3D3D3D", "#F7F7F7"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stMarkdown, .stDataFrame, button, input, textarea, select {{
  font-family: Equinor, Inter, "Segoe UI", Arial, sans-serif !important; }}
.block-container {{ padding-top: 1.1rem; padding-bottom: 4.2rem; max-width: 1500px; }}
h1, h2, h3, h4, h5 {{ color: {SLATE}; letter-spacing: -0.01em; }}
.pfs-header {{ display: flex; align-items: center; gap: 16px; padding: 14px 20px; margin: 0 0 10px 0;
  border-radius: 10px; background: linear-gradient(100deg, {MOSS_DARK} 0%, {MOSS} 62%, #0B8A94 100%);
  color: #fff; box-shadow: 0 2px 10px rgba(0,79,85,.18); position: relative; overflow: hidden; }}
.pfs-header::after {{ content: ""; position: absolute; left: 0; bottom: 0; height: 4px; width: 100%;
  background: linear-gradient(90deg, {ENERGY_RED} 0 18%, transparent 18%); }}
.pfs-mark {{ width: 40px; height: 40px; flex: 0 0 40px; border-radius: 50%; background: rgba(255,255,255,.14);
  display: flex; align-items: center; justify-content: center; }}
.pfs-title {{ font-size: 1.45rem; font-weight: 700; line-height: 1.15; margin: 0; color: #fff; }}
.pfs-sub {{ font-size: .86rem; opacity: .92; margin-top: 2px; }}
.pfs-badge {{ margin-left: auto; font-size: .74rem; font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
  border: 1px solid rgba(255,255,255,.55); border-radius: 999px; padding: 4px 10px; white-space: nowrap; }}
.pfs-footer {{ position: fixed; left: 0; right: 0; bottom: 0; z-index: 999; background: rgba(255,255,255,.96);
  border-top: 3px solid {MOSS}; padding: 6px 18px; font-size: .78rem; color: {TEXT};
  display: flex; gap: 14px; flex-wrap: wrap; align-items: center; }}
.pfs-footer b {{ color: {SLATE}; }}
.pfs-footer .dot {{ width: 8px; height: 8px; border-radius: 50%; background: {ENERGY_RED}; display: inline-block; }}
.stTabs [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid #DCDCDC; }}
.stTabs [data-baseweb="tab"] {{ padding: 8px 14px; border-radius: 6px 6px 0 0; font-weight: 500; }}
.stTabs [aria-selected="true"] {{ color: {MOSS} !important; background: {MOSS_LIGHT}; }}
.stTabs [data-baseweb="tab-highlight"] {{ background-color: {MOSS} !important; }}
[data-testid="stMetric"] {{ background: #fff; border: 1px solid #E3E3E3; border-left: 4px solid {MOSS};
  border-radius: 8px; padding: 10px 14px; }}
[data-testid="stMetricLabel"] p {{ color: #6F6F6F; font-size: .8rem; }}
[data-testid="stMetricValue"] {{ color: {SLATE}; font-size: 1.35rem; }}
[data-testid="stSidebar"] {{ background: {BG2}; border-right: 1px solid #E3E3E3; }}
.pfs-side-credit {{ font-size: .76rem; color: #6F6F6F; border-top: 1px solid #E3E3E3; padding-top: 8px; margin-top: 6px; }}
.stButton > button[kind="primary"] {{ background: {MOSS}; border-color: {MOSS}; }}
.stButton > button[kind="primary"]:hover {{ background: {MOSS_DARK}; border-color: {MOSS_DARK}; }}
@media (max-width: 640px) {{ .pfs-badge {{ display: none; }} .pfs-title {{ font-size: 1.15rem; }}
  .pfs-footer {{ font-size: .7rem; }} }}
@media print {{ .pfs-footer {{ position: static; }} }}
</style>
"""

MARK_SVG = ("<svg width='24' height='24' viewBox='0 0 24 24' fill='none' stroke='white' stroke-width='1.8' "
            "stroke-linecap='round' stroke-linejoin='round'><rect x='3' y='4' width='6' height='16' rx='3'/>"
            "<path d='M9 9h4l3-3h5M9 15h4l3 3h5'/></svg>")


def apply_theme():
    st.markdown(CSS, unsafe_allow_html=True)


def header():
    st.markdown(
        f"<div class='pfs-header'><div class='pfs-mark'>{MARK_SVG}</div><div>"
        f"<div class='pfs-title'>{APP}</div>"
        f"<div class='pfs-sub'>Steady-state process simulation · Peng-Robinson · drag-and-drop PFD</div></div>"
        f"<div class='pfs-badge'>Educational use only</div></div>", unsafe_allow_html=True)


def footer():
    st.markdown(
        f"<div class='pfs-footer'><span class='dot'></span><span>Made by <b>{AUTHOR}</b></span>"
        f"<span>·</span><span>{FOOTER_NOTE}</span></div>", unsafe_allow_html=True)


def sidebar_credit():
    st.markdown(f"<div class='pfs-side-credit'>Made by <b>{AUTHOR}</b><br>For educational purposes only.</div>",
                unsafe_allow_html=True)
