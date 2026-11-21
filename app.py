import json, re, time
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from shapely.geometry import Point, shape

try:
    from streamlit_geolocation import streamlit_geolocation
except Exception:
    streamlit_geolocation = None

st.set_page_config(page_title="Clearway Cardiff", page_icon="🚶", layout="wide", initial_sidebar_state="collapsed")

BASE = Path(__file__).parent
NAVY, TEAL, SAND, CLAY, OCHRE, INK = "#0E3B43", "#1F6F78", "#F7F3EA", "#A8553B", "#C98B2B", "#1D2B33"
SCALE = [[0, CLAY], [0.5, "#EADFC8"], [1, TEAL]]
PRIOR = 5  # the audit counts as this many reports
INDICATORS = {"Q1": "Footway condition", "Q2": "Street clutter", "Q3": "Pavement parking", "Q4": "Lip-free dropped kerb",
              "Q5": "Dropped kerb present", "Q6": "Tactile paving present", "Q7": "Tactile paving condition",
              "Q8": "Footway width", "Q9": "Signage", "Q12": "No street furniture obstruction"}

st.markdown(f"""<style>
@import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:wght@600;700&family=Inter:wght@400;500;600&display=swap');
html,body,[class*="css"]{{font-family:'Inter',sans-serif;color:{INK}}}
h1,h2,h3{{font-family:'Source Serif 4',Georgia,serif;color:{NAVY};letter-spacing:-.01em}}
#MainMenu,footer,[data-testid="stToolbar"],[data-testid="stDecoration"]{{visibility:hidden;height:0}}
header[data-testid="stHeader"]{{background:#fff;border-bottom:1px solid #E3DCCB}}
.block-container{{max-width:1120px;padding-top:2rem}}
.hero{{background:linear-gradient(135deg,{NAVY} 0%,{TEAL} 100%);color:#fff;border-radius:20px;padding:64px 48px;margin-bottom:28px}}
.hero h1{{color:#fff;font-size:3rem;margin:0 0 12px}}.hero p{{font-size:1.2rem;max-width:660px;opacity:.95;margin:0}}
.eyebrow{{text-transform:uppercase;letter-spacing:.14em;font-size:.8rem;color:#E9D8B0;margin-bottom:10px}}
.card{{background:#fff;border:1px solid #E3DCCB;border-radius:16px;padding:22px;height:100%}}
.card h3{{margin-top:0;font-size:1.25rem}}.num{{font-family:'Source Serif 4',serif;font-size:2.2rem;color:{TEAL};font-weight:700;line-height:1.1}}
.lab{{color:#5B6A72;font-size:.9rem}}.pill{{display:inline-block;background:#E8F1F0;color:{NAVY};padding:3px 12px;border-radius:99px;font-size:.85rem;margin-right:6px}}
.foot{{margin-top:48px;padding-top:16px;border-top:1px solid #E3DCCB;color:#5B6A72;font-size:.85rem}}
div[data-testid="stMetric"]{{background:#fff;border:1px solid #E3DCCB;border-radius:14px;padding:14px 18px}}
.stButton>button,.stFormSubmitButton>button{{background:{TEAL};color:#fff;border:0;border-radius:10px;padding:.55rem 1.3rem;font-weight:600}}
.stButton>button:hover,.stFormSubmitButton>button:hover{{background:{NAVY};color:#fff}}
a:focus,button:focus-visible{{outline:3px solid {OCHRE}!important}}
</style>""", unsafe_allow_html=True)


# ---------------------------------------------------------------- data
@st.cache_data
def load():
    geo = json.loads((BASE / "data/cardiff_lsoa.geojson").read_text())
    raw = pd.read_csv(BASE / "data/audit_streets.csv", dtype=str)
    num = raw.copy()
    for q in INDICATORS:
        if q in ("Q5", "Q6"):
            num[q] = (raw[q] == "Yes") * 1.0
        elif q == "Q12":
            num[q] = (raw[q] == "No") * 1.0
        else:
            num[q] = raw[q].astype(float)
    for c in ("wimd_rank", "phys_rank", "access_rank", "sladi_rank"):
        num[c] = raw[c].astype(float)
    num["sladi_score"] = raw["sladi_score"].astype(float)
    base = pd.DataFrame([{**f["properties"]} for f in geo["features"]]).set_index("code")
    agg = num.groupby("lsoa_code").agg(c=("Q2", "mean"), p=("Q3", "mean"), sladi=("sladi_score", "first"),
                                       sladi_rank=("sladi_rank", "first"), wimd_rank=("wimd_rank", "first"),
                                       phys_rank=("phys_rank", "first"), access_rank=("access_rank", "first"))
    return geo, raw, num, base.join(agg)


@st.cache_resource
def polygons():
    return [(f["properties"]["code"], shape(f["geometry"])) for f in load()[0]["features"]]


def sb():
    try:
        return st.secrets["SUPABASE_URL"].rstrip("/"), st.secrets["SUPABASE_KEY"]
    except Exception:
        return None, None


def headers(k):
    return {"apikey": k, "Authorization": f"Bearer {k}", "Content-Type": "application/json"}


@st.cache_data(ttl=60)
def live():
    url, key = sb()
    if not url:
        return None
    try:
        r = requests.post(f"{url}/rest/v1/rpc/lsoa_live", headers=headers(key), json={}, timeout=10)
        r.raise_for_status()
        df = pd.DataFrame(r.json(), columns=["lsoa", "kind", "n", "avg", "morning", "afternoon", "evening"])
        return df.astype({"n": float, "avg": float, "morning": float, "afternoon": float, "evening": float})
    except Exception:
        return None


def lsoa_table():
    L = load()[3].copy()
    lv = live()
    for kind, col, b in (("clutter", "clutter", "c"), ("parking", "parking", "p")):
        sub = lv[lv.kind == kind].set_index("lsoa") if lv is not None and len(lv) else None
        n = sub["n"].reindex(L.index).fillna(0) if sub is not None else pd.Series(0.0, index=L.index)
        a = sub["avg"].reindex(L.index) if sub is not None else pd.Series(float("nan"), index=L.index)
        L[col] = ((PRIOR * L[b] + n * a.fillna(0)) / (PRIOR + n)).where(L[b].notna(), a)
        L[col + "_n"] = n
    return L


def fmt(c):
    L = load()[3]
    return f"{L.loc[c, 'name']} ({c})"


# ---------------------------------------------------------------- maps and charts
def choropleth(L, col, label, rng, dec=2):
    d = L.reset_index()
    fig = px.choropleth_map(d, geojson=load()[0], locations="code", featureidkey="properties.code", color=col,
                            color_continuous_scale=SCALE, range_color=rng, map_style="carto-positron", zoom=10.4,
                            center={"lat": 51.492, "lon": -3.19}, opacity=0.85, hover_name="name",
                            hover_data={"code": False, col: f":.{dec}f"}, labels={col: label})
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), height=560, coloraxis_colorbar=dict(title=label, thickness=14))
    return fig


def compliance_chart():
    num = load()[2]
    rows = []
    for q, name in INDICATORS.items():
        for v, lab in ((0, "Non-compliant"), (0.5, "Partial"), (1, "Compliant")):
            rows.append({"Indicator": name, "Status": lab, "Share": (num[q] == v).mean() * 100})
    d = pd.DataFrame(rows)
    order = d[d.Status == "Compliant"].sort_values("Share")["Indicator"].tolist()
    fig = px.bar(d, y="Indicator", x="Share", color="Status", orientation="h", category_orders={"Indicator": order, "Status": ["Non-compliant", "Partial", "Compliant"]},
                 color_discrete_map={"Non-compliant": CLAY, "Partial": "#E0C48A", "Compliant": TEAL})
    fig.update_layout(height=430, margin=dict(l=0, r=0, t=10, b=0), xaxis_title="% of 406 audited streets", yaxis_title=None,
                      legend_title=None, legend_orientation="h", legend_y=1.1, plot_bgcolor="#fff")
    return fig


def footer():
    st.markdown('<div class="foot">Clearway Cardiff turns an MSc street-level accessibility audit into a public tool. '
                'Contains data from the Welsh Index of Multiple Deprivation 2025 and ONS boundaries, used under the Open Government Licence.</div>',
                unsafe_allow_html=True)


# ---------------------------------------------------------------- pages
def home():
    lv = live()
    total = int(lv["n"].sum()) if lv is not None and len(lv) else 0
    st.markdown('<div class="hero"><div class="eyebrow">Street accessibility in Cardiff</div><h1>Clearway Cardiff</h1>'
                '<p>Every neighbourhood in Cardiff, scored on how easy its streets are to walk for blind and partially sighted people. '
                'Explore the audit, then tell us what you see on your own street.</p></div>', unsafe_allow_html=True)
    c = st.columns(4)
    for col, (n, l) in zip(c, [("203", "neighbourhoods audited"), ("406", "streets surveyed"), ("12", "indicators per street"), (str(total), "community reports (last 6 months)")]):
        col.markdown(f'<div class="card"><div class="num">{n}</div><div class="lab">{l}</div></div>', unsafe_allow_html=True)
    st.write("")
    c = st.columns(3)
    for col, (t, d) in zip(c, [("Explore the data", "Layered maps of the audit, the full street-by-street dataset and how it compares with deprivation."),
                               ("Know your LSOA", "Enter a postcode, street or use your location to find your neighbourhood area."),
                               ("Report what you see", "Log pavement parking and street clutter in under a minute. No account needed.")]):
        col.markdown(f'<div class="card"><h3>{t}</h3><p>{d}</p></div>', unsafe_allow_html=True)
    st.write("")
    st.subheader("How it works")
    st.markdown("The street audit sets a baseline for every neighbourhood. Two measures, pavement parking and street clutter, change as the public "
                f"reports them. Each live score blends the audit result with recent reports, so a handful of reports nudges the map and "
                f"many reports move it. All other indicators are fixed and refreshed at each half-yearly audit.")
    footer()


def dashboard():
    geo, raw, num, _ = load()
    L = lsoa_table()
    st.title("Dashboard")
    lv = live()
    c = st.columns(4)
    c[0].metric("LSOAs audited", int(L["sladi"].notna().sum()))
    c[1].metric("Streets audited", len(raw))
    c[2].metric("Mean SLADI score", f"{L['sladi'].mean():.3f}")
    c[3].metric("Live reports (6 months)", int(lv["n"].sum()) if lv is not None and len(lv) else 0)
    layer = st.radio("Map layer", ["SLADI score", "SLADI rank", "WIMD rank", "Street clutter (live)", "Pavement parking (live)"], horizontal=True)
    cfg = {"SLADI score": ("sladi", "SLADI score", (0, 1), 3), "SLADI rank": ("sladi_rank", "SLADI rank", (1, 203), 0),
           "WIMD rank": ("wimd_rank", "WIMD rank", (1, 203), 0), "Street clutter (live)": ("clutter", "Clutter score", (0, 1), 2),
           "Pavement parking (live)": ("parking", "Parking score", (0, 1), 2)}[layer]
    st.plotly_chart(choropleth(L, cfg[0], cfg[1], cfg[2], cfg[3]), width="stretch")
    st.caption("Clay = more accessibility deprivation (lower scores, lower ranks). Teal = clearer streets. Rank 1 is the most deprived. "
               f"{int(L['sladi'].isna().sum())} LSOAs in the 2021 boundary file have no audit data yet and appear blank.")
    st.subheader("How each indicator performs across all 406 streets")
    st.plotly_chart(compliance_chart(), width="stretch")
    st.subheader("Accessibility against deprivation")
    d = L.dropna(subset=["sladi_rank"]).reset_index()
    fig = px.scatter(d, x="wimd_rank", y="sladi_rank", hover_name="name", color_discrete_sequence=[TEAL], opacity=0.75,
                     labels={"wimd_rank": "WIMD rank within Cardiff (1 = most deprived)", "sladi_rank": "SLADI rank (1 = most deprived)"})
    fig.update_layout(height=420, plot_bgcolor="#fff", margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, width="stretch")
    st.caption("The dots show no pattern: street accessibility cannot be predicted from general deprivation (Spearman rho = -0.066, not significant).")
    st.subheader("Explore one neighbourhood")
    sel = st.selectbox("Choose an LSOA", sorted(raw["lsoa_code"].unique(), key=lambda c: L.loc[c, "name"]), format_func=fmt)
    r = raw[raw.lsoa_code == sel].rename(columns={**INDICATORS, "street": "Street", "wimd_rank": "WIMD rank", "phys_rank": "Physical env. rank",
                                                  "access_rank": "Access rank", "sladi_score": "SLADI score", "sladi_rank": "SLADI rank"})
    st.dataframe(r.drop(columns=["lsoa_code", "area", "Q10", "Q11"]).set_index("Street").T, width="stretch")
    st.caption("Q10 (crossing type) and Q11 (bus stop presence) were recorded as context and are excluded from the score.")
    st.subheader("Full audit dataset")
    q = st.text_input("Filter by street or area name")
    t = raw if not q else raw[raw["street"].str.contains(q, case=False) | raw["area"].str.contains(q, case=False)]
    st.dataframe(t, width="stretch", height=380)
    st.download_button("Download full dataset (CSV)", raw.to_csv(index=False), "clearway_audit_406_streets.csv", "text/csv")
    footer()


def lsoas_at(lat, lon):
    p = Point(lon, lat)
    return [c for c, g in polygons() if g.contains(p)]


@st.cache_data(ttl=86400)
def by_postcode(pc):
    r = requests.get(f"https://api.postcodes.io/postcodes/{quote(pc)}", timeout=8)
    if not r.ok:
        return []
    x = r.json()["result"]
    return lsoas_at(x["latitude"], x["longitude"])


@st.cache_data(ttl=86400)
def by_street(name):
    found = [c for c in load()[1][load()[1]["street"].str.contains(re.escape(name), case=False)]["lsoa_code"]]
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search", timeout=10,
                         params={"q": f"{name}, Cardiff", "format": "jsonv2", "polygon_geojson": 1, "limit": 10, "countrycodes": "gb",
                                 "viewbox": "-3.35,51.60,-3.05,51.37", "bounded": 1},
                         headers={"User-Agent": "ClearwayCardiff/1.0 (accessibility project)"})
        for x in r.json():
            if x.get("category") == "highway":
                g = shape(x["geojson"])
                found += [c for c, poly in polygons() if poly.intersects(g)]
    except Exception:
        pass
    return list(dict.fromkeys(found))


def know():
    st.title("Know your LSOA")
    st.write("An LSOA is a small neighbourhood area of around 1,500 people, the unit used for official statistics. "
             "Enter a Cardiff postcode or street name. Streets that cross several LSOAs show every option.")
    q = st.text_input("Postcode or street name", placeholder="e.g. CF24 3AA or Hornchurch Close")
    c1, c2 = st.columns([1, 3])
    go = c1.button("Find my LSOA")
    with c2:
        loc = streamlit_geolocation() if streamlit_geolocation else None
        st.caption("Or use your location. It is checked on the server only to find the LSOA and is not stored.")
    if go and q.strip():
        q = q.strip()
        pc = re.fullmatch(r"[A-Za-z]{1,2}\d[A-Za-z\d]?\s*\d[A-Za-z]{2}", q)
        st.session_state["kres"] = by_postcode(q) if pc else by_street(q)
        st.session_state["kmiss"] = not st.session_state["kres"]
    elif loc and loc.get("latitude"):
        st.session_state["kres"] = lsoas_at(loc["latitude"], loc["longitude"])
        st.session_state["kmiss"] = not st.session_state["kres"]
    if st.session_state.get("kmiss"):
        st.warning("No Cardiff LSOA found. Check the spelling or try a postcode.")
    L = lsoa_table()
    for code in st.session_state.get("kres", []):
        r = L.loc[code]
        with st.container(border=True):
            st.subheader(f"{r['name']}")
            st.caption(f"LSOA code {code}")
            if pd.notna(r["sladi"]):
                m = st.columns(4)
                m[0].metric("SLADI rank", f"{int(r['sladi_rank'])} of 203")
                m[1].metric("WIMD rank", f"{int(r['wimd_rank'])} of 203")
                m[2].metric("Street clutter", f"{r['clutter']:.2f}")
                m[3].metric("Pavement parking", f"{r['parking']:.2f}")
                st.caption("Each LSOA score rests on two audited streets plus recent community reports.")
            else:
                st.info("This LSOA has not been audited yet. Community reports are still welcome.")
            if st.button("Report in this LSOA", key=f"rep{code}"):
                st.session_state["rep_lsoa"] = code
                st.switch_page(PAGES["report"])
    footer()


CLUTTER = {"Not reporting clutter": None, "0: a lot of clutter": 0, "0.5: at least one item": 0.5, "1: clear, no clutter": 1}
PARKING = {"Not reporting parking": None, "0: more than 2 cars on the pavement": 0, "0.5: at least 1 car": 0.5, "1: no cars": 1}


def report():
    st.title("Report what you saw")
    st.write("Rate one or both. It takes under a minute and needs no account. Not sure which LSOA you are in? Use the Know your LSOA page.")
    L = load()[3]
    codes = sorted(L.index, key=lambda c: L.loc[c, "name"])
    with st.form("report"):
        code = st.selectbox("Neighbourhood (LSOA)", codes, format_func=fmt, key="rep_lsoa")
        a = st.radio("Street clutter (bins, signs, boards, other items)", list(CLUTTER), index=0)
        b = st.radio("Pavement parking", list(PARKING), index=0)
        t = st.radio("Time of day", ["Morning", "Afternoon", "Evening"], index=None, horizontal=True)
        ok = st.form_submit_button("Send report")
    if ok:
        rows = [{"lsoa": code, "kind": k, "severity": v[x], "tod": (t or "").lower()}
                for k, v, x in (("clutter", CLUTTER, a), ("parking", PARKING, b)) if v[x] is not None]
        url, key = sb()
        if not rows or not t:
            st.error("Choose at least one rating and a time of day.")
        elif time.time() - st.session_state.get("last", 0) < 60:
            st.warning("Please wait a minute before sending another report.")
        elif not url:
            st.error("Live reporting is not connected yet.")
        else:
            r = requests.post(f"{url}/rest/v1/reports", headers={**headers(key), "Prefer": "return=minimal"}, json=rows, timeout=10)
            if r.ok:
                st.session_state["last"] = time.time()
                live.clear()
                st.success("Thank you. Your report has been added to the maps and complaints board.")
            else:
                st.error(f"Could not save the report (error {r.status_code}).")
    footer()


def complaints():
    st.title("Complaints board")
    lv = live()
    L = load()[3]
    if lv is None:
        st.info("Live reporting is not connected yet. Counts will appear here once it is.")
        lv = pd.DataFrame(columns=["lsoa", "kind", "n", "avg", "morning", "afternoon", "evening"]).astype(
            {"n": float, "avg": float, "morning": float, "afternoon": float, "evening": float})
    area = st.selectbox("Area", ["All Cardiff"] + sorted(L.index, key=lambda c: L.loc[c, "name"]), format_func=lambda c: c if c == "All Cardiff" else fmt(c))
    d = lv if area == "All Cardiff" else lv[lv.lsoa == area]
    tot = d.groupby("kind")[["n", "morning", "afternoon", "evening"]].sum().reindex(["clutter", "parking"]).fillna(0)
    m = st.columns(4)
    m[0].metric("Total reports", int(tot["n"].sum()))
    m[1].metric("Street clutter", int(tot.loc["clutter", "n"]))
    m[2].metric("Pavement parking", int(tot.loc["parking", "n"]))
    m[3].metric("LSOAs with reports", d["lsoa"].nunique())
    tt = pd.DataFrame({"Time of day": ["Morning", "Afternoon", "Evening"] * 2,
                       "Type": ["Street clutter"] * 3 + ["Pavement parking"] * 3,
                       "Reports": [tot.loc[k, c] for k in ("clutter", "parking") for c in ("morning", "afternoon", "evening")]})
    fig = px.bar(tt, x="Time of day", y="Reports", color="Type", barmode="group", color_discrete_map={"Street clutter": OCHRE, "Pavement parking": TEAL})
    fig.update_layout(height=340, plot_bgcolor="#fff", margin=dict(l=0, r=0, t=10, b=0), legend_title=None)
    st.plotly_chart(fig, width="stretch")
    st.subheader("Most reports across Cardiff")
    top = lv.groupby("lsoa")["n"].sum().sort_values(ascending=False).head(10).reset_index()
    if len(top):
        top["LSOA"] = top["lsoa"].map(lambda c: L.loc[c, "name"] if c in L.index else c)
        fig = px.bar(top.iloc[::-1], x="n", y="LSOA", orientation="h", color_discrete_sequence=[CLAY], labels={"n": "Reports"})
        fig.update_layout(height=380, plot_bgcolor="#fff", margin=dict(l=0, r=0, t=10, b=0), yaxis_title=None)
        st.plotly_chart(fig, width="stretch")
    else:
        st.write("No reports yet.")
    st.caption("Counts cover the last six months. They show what people chose to report, which reflects both conditions and who is reporting.")
    footer()


def findings():
    st.title("Findings")
    easy = st.toggle("Easy Read version")
    if easy:
        st.markdown("""
### What we did
We visited 406 streets in Cardiff. We looked at 2 streets in each of 203 neighbourhoods.

### What we checked
Is the pavement clear, smooth and safe for blind and partially sighted people?

### What we found
- Most streets have dropped kerbs and tactile paving.
- Many streets have things in the way, such as cars and clutter.
- Almost all streets have poor signage.
- Streets in richer areas are not always better. Streets in poorer areas are not always worse.

### Why it matters
Money spent only on poorer areas would not fix the worst pavements. Cardiff needs to look at every street.
""")
        footer()
        return
    st.write("The Street-Level Accessibility Deprivation Index (SLADI) scores streets on ten barriers that matter to blind and partially "
             "sighted pedestrians. It was built from a walking audit of 406 streets, two in each of 203 Cardiff LSOAs, and tested against the "
             "Welsh Index of Multiple Deprivation (WIMD) 2025.")
    m = st.columns(4)
    m[0].metric("Mean SLADI score", "0.704")
    m[1].metric("Best street area", "Cathays 6 (1.00)")
    m[2].metric("Lowest street area", "Canton 7 (0.045)")
    m[3].metric("SLADI and WIMD (rho)", "-0.066")
    st.subheader("1. Compliance is uneven, and signage is the weakest point")
    st.plotly_chart(compliance_chart(), width="stretch")
    st.markdown("Dropped kerbs (94.6 percent) and tactile paving (96.6 percent) are close to universal because they are statutory design requirements. "
                "Signage fails on 79.6 percent of streets. Only 45.3 percent of streets were clear of clutter and 63.1 percent free of pavement parking, "
                "the two measures that the public can report on this site. Nationally, RNIB (2025) found that 82 percent of blind and partially sighted "
                "people see vehicles parked on pavements as a major barrier.")
    st.subheader("2. Street accessibility does not follow deprivation")
    st.markdown("The association between SLADI rank and WIMD overall rank is almost zero (rho = -0.066, p = 0.352), and the same holds for the "
                "Physical Environment (rho = -0.035) and Access to Services (rho = -0.026) domains. Median SLADI scores are nearly flat across the "
                "five deprivation quintiles (Kruskal-Wallis H = 4.38, p = 0.357), and there is no spatial clustering overall "
                "(Moran's I = 0.007, against an expected value of -0.005).")
    q = pd.DataFrame({"WIMD quintile": ["1 (most deprived)", "2", "3", "4", "5 (least deprived)"], "Median SLADI": [0.7368, 0.7434, 0.75, 0.7368, 0.7105]})
    fig = px.bar(q, x="WIMD quintile", y="Median SLADI", color_discrete_sequence=[TEAL], range_y=[0, 1])
    fig.update_layout(height=320, plot_bgcolor="#fff", margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, width="stretch")
    st.subheader("3. Where the gap is largest")
    c = st.columns(2)
    c[0].markdown("**Hidden deprivation** (affluent on WIMD, poor streets)")
    c[0].dataframe(pd.DataFrame({"LSOA": ["Rhiwbina 5", "Llandaff 5", "Heath 6", "Cyncoed 2", "Whitchurch 1"], "WIMD rank": [186, 201, 180, 197, 198],
                                 "SLADI rank": [8, 28, 11, 30, 31]}), hide_index=True, width="stretch")
    c[1].markdown("**Compensatory accessibility** (deprived on WIMD, good streets)")
    c[1].dataframe(pd.DataFrame({"LSOA": ["Caerau 2", "Caerau 6", "Trowbridge 5", "Ely 5", "Trowbridge 8"], "WIMD rank": [17, 25, 28, 1, 2],
                                 "SLADI rank": [200, 188, 188, 154, 154]}), hide_index=True, width="stretch")
    st.caption("Ranks are within Cardiff; rank 1 is the most deprived.")
    st.subheader("4. What it means")
    st.markdown("Accessibility deprivation is a separate form of disadvantage that WIMD cannot reveal. Targeting street improvements by socio-economic "
                "deprivation alone would do no better than chance, so Cardiff needs street-level evidence like this to decide where to act.")
    st.subheader("5. Limits")
    st.markdown("Each LSOA is represented by two audited streets, observed on a single visit, so transient obstacles such as pavement parking and "
                "clutter are snapshots. That is why those two measures are updated live from public reports.")
    footer()


PAGES = {"home": st.Page(home, title="Home", default=True), "dashboard": st.Page(dashboard, title="Dashboard"),
         "know": st.Page(know, title="Know your LSOA"), "report": st.Page(report, title="Report"),
         "complaints": st.Page(complaints, title="Complaints board"), "findings": st.Page(findings, title="Findings")}
try:
    nav = st.navigation(list(PAGES.values()), position="top")
except TypeError:
    nav = st.navigation(list(PAGES.values()))
nav.run()
