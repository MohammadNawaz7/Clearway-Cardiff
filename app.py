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
st.session_state.setdefault("L", "English")


def t(en, cy):
    return cy if st.session_state.get("L") == "Cymraeg" else en


def _setlang():
    st.session_state["L"] = st.session_state.get("lang_w") or "English"


def topbar():
    c = st.columns([6, 2])
    c[1].segmented_control("Language / Iaith", ["English", "Cymraeg"], key="lang_w", default=st.session_state["L"],
                           on_change=_setlang, label_visibility="collapsed")

NAVY, TEAL, SAND, CLAY, OCHRE, INK = "#0A3D24", "#00843D", "#FBF8F1", "#C8102E", "#C98B2B", "#1B2A22"
CASTLE = "https://commons.wikimedia.org/wiki/Special:FilePath/Cardiff_Castle_-_2022-08-17.jpg?width=1600"
FLAG = "https://commons.wikimedia.org/wiki/Special:FilePath/Flag_of_Wales.svg?width=200"
SCALE = [[0, CLAY], [0.5, "#F3EBD3"], [1, TEAL]]
PRIOR = 5  # the audit counts as this many reports
INDICATORS = {"Q1": "Footway condition", "Q2": "Street clutter", "Q3": "Pavement parking", "Q4": "Lip-free dropped kerb",
              "Q5": "Dropped kerb present", "Q6": "Tactile paving present", "Q7": "Tactile paving condition",
              "Q8": "Footway width", "Q9": "Signage", "Q12": "No street furniture obstruction"}

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:wght@600;700&family=Inter:wght@400;500;600&display=swap');
html,body,[class*="css"]{font-family:'Inter',sans-serif;color:@INK}
h1,h2,h3{font-family:'Source Serif 4',Georgia,serif;color:@NAVY;letter-spacing:-.01em}
#MainMenu,footer,[data-testid="stAppDeployButton"],[data-testid="stToolbarActions"],[data-testid="stDecoration"]{display:none!important}
header[data-testid="stHeader"]{background:#fff;border-bottom:3px solid @RED;box-shadow:0 2px 10px rgba(0,0,0,.06)}
header[data-testid="stHeader"]::before{content:"Clearway Cardiff";position:absolute;left:28px;top:50%;transform:translateY(-50%);
 font:700 1.25rem 'Source Serif 4',Georgia,serif;color:@NAVY}
.block-container{max-width:1120px;padding-top:2rem}
.hero{background:linear-gradient(100deg,rgba(10,61,36,.95) 0%,rgba(10,61,36,.80) 42%,rgba(10,61,36,.15) 100%),url(@CASTLE) center/cover,@NAVY;
 color:#fff;border-radius:26px;padding:84px 52px;min-height:430px;margin-bottom:18px;box-shadow:0 14px 40px rgba(10,61,36,.25)}
.hero h1{color:#fff;font-size:3.6rem;margin:14px 0 14px;line-height:1.05}.hero p{font-size:1.2rem;max-width:560px;margin:0;opacity:.96}
.hero img{height:30px;border-radius:4px;border:1px solid rgba(255,255,255,.6);vertical-align:middle;margin-right:12px}
.eyebrow{text-transform:uppercase;letter-spacing:.16em;font-size:.8rem;color:#F1E3B8;font-weight:600}
.stripe{height:6px;border-radius:3px;margin:6px 0 26px;background:linear-gradient(90deg,#fff 0 33%,@TEAL 33% 66%,@RED 66%);border:1px solid #E5DECF}
.tile{font-size:2rem;line-height:1}.tile+h3{margin:.3rem 0 .2rem;font-size:1.2rem}.lab{color:#5B6A72;font-size:.95rem}
div[data-testid="stVerticalBlockBorderWrapper"]{border-radius:18px;background:#fff;border-color:#E5DECF}
.band{background:@NAVY;color:#fff;border-radius:20px;padding:34px 40px;margin-top:22px;font-family:'Source Serif 4',serif;font-size:1.45rem;line-height:1.4}
.band small{display:block;margin-top:10px;font:500 .9rem Inter,sans-serif;opacity:.8}
.foot{margin-top:48px;padding-top:16px;border-top:1px solid #E5DECF;color:#5B6A72;font-size:.85rem}
div[data-testid="stMetric"]{background:#fff;border:1px solid #E5DECF;border-radius:14px;padding:14px 18px}
.stButton>button,.stFormSubmitButton>button{background:@TEAL;color:#fff;border:0;border-radius:12px;padding:.6rem 1.4rem;font-weight:600}
.stButton>button:hover,.stFormSubmitButton>button:hover{background:@NAVY;color:#fff}
.stButton>button[kind="primary"],button[data-testid="stBaseButton-primary"]{background:@RED;color:#fff}
a:focus,button:focus-visible{outline:3px solid @OCHRE!important}
"""
for _k, _v in {"@INK": INK, "@NAVY": NAVY, "@TEAL": TEAL, "@RED": CLAY, "@OCHRE": OCHRE, "@CASTLE": CASTLE}.items():
    CSS = CSS.replace(_k, _v)
st.markdown("<style>" + CSS + "</style>", unsafe_allow_html=True)


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
                'Contains data from the Welsh Index of Multiple Deprivation 2025 and ONS boundaries, used under the Open Government Licence. '
                'Cardiff Castle photo: The wub, CC BY-SA 4.0, via Wikimedia Commons. Flag of Wales: Wikimedia Commons.</div>',
                unsafe_allow_html=True)


# ---------------------------------------------------------------- pages
def home():
    topbar()
    eyebrow = t("Street accessibility in Cardiff", "Hygyrchedd strydoedd yng Nghaerdydd")
    lead = t("How easy is your street to walk? See how every Cardiff neighbourhood scores for blind and partially sighted pedestrians, and tell us what you see on your own street.",
             "Pa mor hawdd yw cerdded eich stryd? Gweler sut mae pob cymdogaeth yng Nghaerdydd yn sgorio i gerddwyr dall a rhannol ddall, a dywedwch wrthym beth welwch chi ar eich stryd eich hun.")
    st.markdown('<div class="hero"><div class="eyebrow"><img src="' + FLAG + '" alt="Flag of Wales">' + eyebrow +
                '</div><h1>Clearway Cardiff</h1><p>' + lead + '</p></div>', unsafe_allow_html=True)
    c = st.columns([1.2, 1.2, 3])
    if c[0].button(t("Report what you see", "Adrodd beth welwch chi"), type="primary"):
        st.switch_page(PAGES["report"])
    if c[1].button(t("Find my neighbourhood", "Dod o hyd i'm cymdogaeth")):
        st.switch_page(PAGES["know"])
    st.markdown('<div class="stripe"></div>', unsafe_allow_html=True)
    tiles = [("🗺️", t("Explore the map", "Archwilio'r map"), t("Layered maps of every neighbourhood and the full street audit.", "Mapiau haenog o bob cymdogaeth a'r archwiliad stryd llawn."), "dashboard"),
             ("📍", t("Know your LSOA", "Adnabod eich LSOA"), t("Find your neighbourhood area from a postcode, street or your location.", "Dewch o hyd i'ch ardal o god post, stryd neu eich lleoliad."), "know"),
             ("📣", t("Complaints board", "Bwrdd cwynion"), t("See what people are reporting, and when.", "Gweler beth mae pobl yn ei adrodd, a phryd."), "complaints"),
             ("📖", t("Findings", "Canfyddiadau"), t("What the research found, with an Easy Read version.", "Beth ddaeth i'r amlwg o'r ymchwil, gyda fersiwn Hawdd ei Darllen."), "findings")]
    for col, (ic, ti, de, pg) in zip(st.columns(4), tiles):
        with col.container(border=True, height=250):
            st.markdown('<div class="tile">' + ic + '</div><h3>' + ti + '</h3><p class="lab">' + de + '</p>', unsafe_allow_html=True)
            if st.button(t("Open", "Agor"), key="t_" + pg):
                st.switch_page(PAGES[pg])
    quote = t("Cars on pavements and clutter on the footway force blind and partially sighted people into the road. Your report keeps this picture honest and current.",
              "Mae ceir ar balmentydd ac annibendod ar y troedffordd yn gorfodi pobl ddall a rhannol ddall i'r ffordd. Mae eich adroddiad yn cadw'r darlun hwn yn gywir ac yn gyfredol.")
    small = t("Reports are anonymous. Only the neighbourhood, type, rating and time of day are stored.",
              "Mae adroddiadau'n ddienw. Dim ond y gymdogaeth, y math, y sgôr ac adeg y dydd a gedwir.")
    st.markdown('<div class="band">' + quote + '<small>' + small + '</small></div>', unsafe_allow_html=True)
    footer()


def dashboard():
    geo, raw, num, _ = load()
    L = lsoa_table()
    topbar()
    st.title(t("Explore the map", "Archwilio'r map"))
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
    tbl = raw if not q else raw[raw["street"].str.contains(q, case=False) | raw["area"].str.contains(q, case=False)]
    st.dataframe(tbl, width="stretch", height=380)
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
    topbar()
    st.title(t("Know your LSOA", "Adnabod eich LSOA"))
    st.write("An LSOA is a small neighbourhood area of around 1,500 people, the unit used for official statistics. "
             "Enter a Cardiff postcode or street name. Streets that cross several LSOAs show every option.")
    q = st.text_input(t("Postcode or street name", "Cod post neu enw stryd"), placeholder="e.g. CF24 3AA or Hornchurch Close")
    c1, c2 = st.columns([1, 3])
    go = c1.button(t("Find my LSOA", "Dod o hyd i'm LSOA"))
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


def opts():
    c = {t("Not reporting clutter", "Ddim yn adrodd annibendod"): None, t("0: a lot of clutter", "0: llawer o annibendod"): 0,
         t("0.5: at least one item", "0.5: o leiaf un eitem"): 0.5, t("1: clear, no clutter", "1: clir, dim annibendod"): 1}
    p = {t("Not reporting parking", "Ddim yn adrodd parcio"): None, t("0: more than 2 cars on the pavement", "0: mwy na 2 gar ar y palmant"): 0,
         t("0.5: at least 1 car", "0.5: o leiaf 1 car"): 0.5, t("1: no cars", "1: dim ceir"): 1}
    return c, p


def report():
    topbar()
    st.title(t("Report what you saw", "Adrodd beth welsoch chi"))
    st.write(t("Rate one or both. It takes under a minute and needs no account. Not sure which LSOA you are in? Use Know your LSOA first.",
               "Graddiwch un neu'r ddau. Mae'n cymryd llai na munud ac nid oes angen cyfrif. Ddim yn siŵr pa LSOA ydych chi ynddo? Defnyddiwch Adnabod eich LSOA yn gyntaf."))
    L = load()[3]
    codes = sorted(L.index, key=lambda c: L.loc[c, "name"])
    CL, PK = opts()
    names = {"morning": t("Morning", "Bore"), "afternoon": t("Afternoon", "Prynhawn"), "evening": t("Evening", "Min nos")}
    with st.form("report"):
        code = st.selectbox(t("Neighbourhood (LSOA)", "Cymdogaeth (LSOA)"), codes, format_func=fmt, key="rep_lsoa")
        a = st.radio(t("Street clutter (bins, signs, boards, other items)", "Annibendod ar y stryd (biniau, arwyddion, byrddau, eitemau eraill)"), list(CL), index=0)
        b = st.radio(t("Pavement parking", "Parcio ar y palmant"), list(PK), index=0)
        tod = st.radio(t("Time of day", "Adeg o'r dydd"), list(names), format_func=names.get, index=None, horizontal=True)
        ok = st.form_submit_button(t("Send report", "Anfon adroddiad"))
    if ok:
        rows = [{"lsoa": code, "kind": k, "severity": v[x], "tod": tod} for k, v, x in (("clutter", CL, a), ("parking", PK, b)) if v[x] is not None]
        url, key = sb()
        if not rows or not tod:
            st.error(t("Choose at least one rating and a time of day.", "Dewiswch o leiaf un sgôr ac adeg o'r dydd."))
        elif time.time() - st.session_state.get("last", 0) < 60:
            st.warning(t("Please wait a minute before sending another report.", "Arhoswch funud cyn anfon adroddiad arall."))
        elif not url:
            st.error(t("Live reporting is not connected yet.", "Nid yw adrodd byw wedi'i gysylltu eto."))
        else:
            r = requests.post(url + "/rest/v1/reports", headers={**headers(key), "Prefer": "return=minimal"}, json=rows, timeout=10)
            if r.ok:
                st.session_state["last"] = time.time()
                live.clear()
                st.success(t("Thank you. Your report has been added to the maps and complaints board.", "Diolch. Ychwanegwyd eich adroddiad at y mapiau a'r bwrdd cwynion."))
            else:
                st.error("Error " + str(r.status_code))
    footer()


def complaints():
    topbar()
    st.title(t("Complaints board", "Bwrdd cwynion"))
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
    m[0].metric(t("Total reports", "Cyfanswm adroddiadau"), int(tot["n"].sum()))
    m[1].metric(t("Street clutter", "Annibendod"), int(tot.loc["clutter", "n"]))
    m[2].metric(t("Pavement parking", "Parcio ar y palmant"), int(tot.loc["parking", "n"]))
    m[3].metric("LSOAs with reports", d["lsoa"].nunique())
    tt = pd.DataFrame({"Time of day": ["Morning", "Afternoon", "Evening"] * 2,
                       "Type": ["Street clutter"] * 3 + ["Pavement parking"] * 3,
                       "Reports": [tot.loc[k, c] for k in ("clutter", "parking") for c in ("morning", "afternoon", "evening")]})
    fig = px.bar(tt, x="Time of day", y="Reports", color="Type", barmode="group", color_discrete_map={"Street clutter": OCHRE, "Pavement parking": TEAL})
    fig.update_layout(height=340, plot_bgcolor="#fff", margin=dict(l=0, r=0, t=10, b=0), legend_title=None)
    st.plotly_chart(fig, width="stretch")
    st.subheader(t("Most reports across Cardiff", "Y mwyaf o adroddiadau yng Nghaerdydd"))
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
    topbar()
    st.title(t("Findings", "Canfyddiadau"))
    easy = st.toggle(t("Easy Read version", "Fersiwn Hawdd ei Darllen"))
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


PAGES = {"home": st.Page(home, title=t("Home", "Hafan"), default=True), "dashboard": st.Page(dashboard, title=t("Explore", "Archwilio")),
         "know": st.Page(know, title=t("Know your LSOA", "Adnabod eich LSOA")), "report": st.Page(report, title=t("Report", "Adrodd")),
         "complaints": st.Page(complaints, title=t("Complaints board", "Bwrdd cwynion")), "findings": st.Page(findings, title=t("Findings", "Canfyddiadau"))}
try:
    nav = st.navigation(list(PAGES.values()), position="top")
except TypeError:
    nav = st.navigation(list(PAGES.values()))
nav.run()
