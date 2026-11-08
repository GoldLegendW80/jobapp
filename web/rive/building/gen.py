#!/usr/bin/env python3
"""Writes web/rive/building/building.rml: the Building artboard of the onboarding wizard."""

import math
import sys

OUT = sys.argv[1]

FPS = 60
P = 150  # loop period, frames (2.5 s)
J = 112  # one sheet's journey, frames
OFFSETS = [0, 50, 100]
FIN = 84  # finale length, frames
CX_END = 240  # the card glides to the centre in the finale

CX, CY = 288, 150  # card rest position
W, H = 150, 200
HW, HH = W / 2, H / 2

X0 = 66  # where a sheet appears
ABSORB = (CX - 30, CY + 4)  # where a sheet dissolves into the card
BADGE = (HW - 10, -HH + 8)  # card-local

WHITE = "FFFFFFFF"
HAIR = "FFE6E6E6"
LINE1 = "FFD6D6D6"
LINE2 = "FFE4E4E4"
BAND = "FFFFC298"
CHIP = "FFFFDECD"
SKEL = "FFF1F1F1"
SKEL_BAND = "FFF5F5F5"
SH1 = "121B1B1B"
SH2 = "0A1B1B1B"
ORANGE = "FFF86F3C"
ORANGE_LIGHT = "FFF88F4F"
ORANGE_DEEP = "FFD4411A"
GOLD = "FFE6AC3D"

_n = [0]


def nid():
    _n[0] += 1
    return f"7:{_n[0]}"


def fmt(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v
    if isinstance(v, int):
        return str(v)
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class E:
    def __init__(self, tag, *children, **kw):
        self.tag = tag
        self.kw = {k.rstrip("_"): v for k, v in kw.items() if v is not None}
        self.children = [c for c in children if c is not None]

    def add(self, *cs):
        self.children.extend(c for c in cs if c is not None)
        return self

    def render(self, ind=0):
        keys = [k for k in self.kw if k not in ("name", "id")] + [
            k for k in ("name", "id") if k in self.kw
        ]
        a = " ".join(f'{k}="{fmt(self.kw[k])}"' for k in keys)
        head = f"<{self.tag}{' ' + a if a else ''}"
        pad = "    " * ind
        if not self.children:
            return f"{pad}{head}/>"
        inner = "\n".join(c.render(ind + 1) for c in self.children)
        return f"{pad}{head}>\n{inner}\n{pad}</{self.tag}>"


def solid(c):
    return E("SolidColor", colorValue=c)


def fill(c):
    return E("Fill", solid(c))


def stroke(c, t):
    return E("Stroke", solid(c), thickness=t, cap="round", join="round")


def rect(w, h, r=0, **kw):
    return E("Rectangle", width=w, height=h, cornerRadiusTL=r if r else None, **kw)


def shape(name, geom, *paints, **kw):
    return E("Shape", geom, *paints, name=name, **kw)


def node(name, *children, **kw):
    return E("Node", *children, name=name, **kw)


def card_shadows(w, h, r, dy1, dy2):
    return [
        shape("Shadow", rect(w + 2, h + 2, r + 1), fill(SH1), y=dy1),
        shape("Shadow far", rect(w + 6, h + 4, r + 3), fill(SH2), y=dy2),
    ]


def smooth(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def bump(u, c, hw):
    d = abs(u - c) / hw
    return 0.0 if d >= 1 else 0.5 * (1 + math.cos(math.pi * d))


# ---------------------------------------------------------------- keyframes


def kf(frame, value, ease=None):
    """ease: None linear, 'hold', or a cubic bezier (x1, y1, x2, y2)."""
    if ease == "hold":
        return E("KeyFrameDouble", value=value, frame=frame, interpolationType="hold")
    if ease is None:
        return E("KeyFrameDouble", value=value, frame=frame, interpolationType="linear")
    x1, y1, x2, y2 = ease
    return E(
        "KeyFrameDouble",
        E("CubicEaseInterpolator", x1=x1, y1=y1, x2=x2, y2=y2),
        value=value,
        frame=frame,
        interpolationType="cubic",
    )


def keyed(obj, props):
    """props: {property: [keyframe, ...]}"""
    return E(
        "KeyedObject",
        *[E("KeyedProperty", *frames, property=p) for p, frames in props.items()],
        objectId=obj,
    )


def sampled(fn, tol, period=P):
    """Linear keyframes approximating fn over 0..period within tol; fn(period) must equal fn(0)."""
    vals = [fn(f) for f in range(period + 1)]
    keep = [0]
    i = 0
    while i < period:
        j = i + 1
        while j + 1 <= period:
            ok = True
            for k in range(i + 1, j + 1):
                t = (k - i) / (j + 1 - i)
                if abs(vals[i] + (vals[j + 1] - vals[i]) * t - vals[k]) > tol:
                    ok = False
                    break
            if not ok:
                break
            j += 1
        keep.append(j)
        i = j
    return [kf(f, round(vals[f], 4)) for f in keep]


# ---------------------------------------------------------------- sheet motion

N = 2000
_v = []
for i in range(N + 1):
    u = i / N
    ramp = 0.55 + 0.45 * smooth(u / 0.16)
    slow = 1 - 0.62 * math.exp(-(((u - 0.5) / 0.12) ** 2))
    land = 1 - 0.55 * smooth((u - 0.78) / 0.22)
    _v.append(ramp * slow * land)
_cum = [0.0]
for i in range(1, N + 1):
    _cum.append(_cum[-1] + (_v[i] + _v[i - 1]) / 2)


def along(u):
    i = min(N, max(0, round(u * N)))
    return _cum[i] / _cum[-1]


XE, YE = ABSORB
XB = X0 + (XE - X0) * along(0.5)

SHEETS = [
    # name, start y, y under the beam, start rotation
    ("CV", 112, 146, -0.16),
    ("LinkedIn", 188, 156, 0.13),
    ("Notes", 140, 150, -0.07),
]
BY = 150  # beam centre


def sheet_u(t, k):
    return ((t - OFFSETS[k]) % P) / J


def sheet_state(u, k):
    _, y0, yb, r0 = SHEETS[k]
    if u > 1:
        u = 0.0
    x = X0 + (XE - X0) * along(u)
    y = (
        y0 + (yb - y0) * smooth(u / 0.5)
        if u < 0.5
        else yb + (YE - yb) * smooth((u - 0.5) / 0.5)
    )
    rot = r0 * (1 - smooth(u / 0.5)) + (-r0 * 0.25) * bump(u, 0.8, 0.2)
    s = (
        0.86 + 0.14 * smooth(u / 0.2)
        if u < 0.6
        else 1 - 0.62 * smooth((u - 0.6) / 0.38)
    )
    o = smooth(u / 0.14) * (1 - smooth((u - 0.74) / 0.2))
    read = smooth((u - 0.4) / 0.16)
    glow = bump(u, 0.5, 0.15)
    return {
        "x": x,
        "y": y,
        "rotation": rot,
        "scale": s,
        "opacity": o,
        "read": read,
        "glow": glow,
    }


def at(t, k, key):
    u = sheet_u(t, k)
    st = sheet_state(u, k)
    if u > 1:
        st["opacity"] = 0.0
        st["read"] = 0.0
        st["glow"] = 0.0
    return st[key]


# ---------------------------------------------------------------- ids

ART, STYLE, SM = nid(), nid(), nid()
VM, P_PROG, P_FIN, VMI = nid(), nid(), nid(), nid()
SMOOTH = nid()
BEAM, BEAM_GLOW, BEAM_CORE, SCAN = nid(), nid(), nid(), nid()
SHEET_IDS = [(nid(), nid(), nid()) for _ in SHEETS]  # node, read, glow
CARD, PULSE, BAND_WARM = nid(), nid(), nid()
BADGE_ID, TRIM, POP_RING = nid(), nid(), nid()
SPARKS = [nid() for _ in range(8)]
A_LOOP, A_FIN = nid(), nid()
S_BLEND, S_FULL, S_LOOP, S_FIN = nid(), nid(), nid(), nid()

# ---------------------------------------------------------------- the card

# Each piece of the profile appears inside its window of progress (0 to 100).
PIECES = []  # (id, name, kind, (a, b), x, y, real shapes, skeleton shapes)


def piece(name, kind, window, x, y, real, skel):
    PIECES.append((nid(), name, kind, window, x, y, real, skel))


def line(w, h, c):
    return shape("Line", rect(w, h, h / 2, originX=0), fill(c))


def chip(w, h, c):
    return shape("Chip", rect(w, h, h / 2), fill(c))


AV = (-36, -HH + 46)
piece(
    "Avatar",
    "pop",
    (5, 18),
    *AV,
    [
        shape("Head", E("Ellipse", width=13, height=13), fill("FFFFF8F3"), y=-5),
        shape("Shoulders", E("Ellipse", width=24, height=14), fill("FFFFF8F3"), y=11),
        shape("Disc", E("Ellipse", width=40, height=40), fill(BAND)),
    ],
    [shape("Disc", E("Ellipse", width=40, height=40), fill("FFEDEDED"))],
)
piece("Name", "line", (20, 32), -62, -14, [line(80, 8, LINE1)], [line(80, 8, SKEL)])
piece("Headline", "line", (26, 40), -62, -1, [line(58, 6, LINE2)], [line(58, 6, SKEL)])
for i, (cx, w, win) in enumerate(
    [(-47, 30, (40, 50)), (-14, 28, (44, 55)), (21, 34, (48, 60))]
):
    piece(
        f"Skill {i + 1}", "pop", win, cx, 20, [chip(w, 12, CHIP)], [chip(w, 12, SKEL)]
    )
for i, (y, w, win) in enumerate([(44, 86, (60, 72)), (58, 64, (68, 80))]):
    piece(
        f"Role {i + 1} mark",
        "pop",
        (win[0], win[0] + 7),
        -58,
        y,
        [chip(8, 8, BAND)],
        [chip(8, 8, SKEL)],
    )
    piece(
        f"Role {i + 1}",
        "line",
        (win[0] + 1, win[1]),
        -48,
        y,
        [line(w, 6, LINE2)],
        [line(w, 6, SKEL)],
    )
piece("Studies", "line", (80, 90), -62, 72, [line(98, 6, LINE2)], [line(98, 6, SKEL)])
piece(
    "Language 1", "pop", (84, 93), -49, 86, [chip(26, 10, CHIP)], [chip(26, 10, SKEL)]
)
piece(
    "Language 2", "pop", (88, 98), -17, 86, [chip(32, 10, CHIP)], [chip(32, 10, SKEL)]
)


def piece_pose(kind, window, p):
    a, b = window
    r = smooth((p - a) / (b - a))
    if kind == "line":
        return {"scaleX": r}
    # pops overshoot a little before they settle
    if r <= 0:
        sc = 0.0
    elif r < 0.75:
        sc = 1.1 * (r / 0.75)
    else:
        sc = 1.1 - 0.1 * ((r - 0.75) / 0.25)
    return {"scaleX": sc, "scaleY": sc}


BREAKS = [float(p) for p in range(0, 101, 5)]


def full_pose_keys(p):
    objs = []
    for pid, _, kind, window, *_ in PIECES:
        objs.append(
            keyed(
                pid,
                {
                    k: [kf(0, round(v, 4), "hold")]
                    for k, v in piece_pose(kind, window, p).items()
                },
            )
        )
    objs.append(keyed(BAND_WARM, {"opacity": [kf(0, round(p / 100, 4), "hold")]}))
    return objs


pose_anims = []
for p in BREAKS:
    aid = nid()
    pose_anims.append((p, aid))

reveal_nodes = []
skeleton = []
for pid, name, kind, window, x, y, real, skel in PIECES:
    init = piece_pose(kind, window, 0)
    reveal_nodes.append(
        node(name, *real, x=x, y=y, id_=pid, **{k: v for k, v in init.items()})
    )
    skeleton.append(node(name, *skel, x=x, y=y))

band_rect = {
    "linkCornerRadius": False,
    "cornerRadiusTL": 16,
    "cornerRadiusTR": 16,
    "cornerRadiusBL": 0,
    "cornerRadiusBR": 0,
}
pulse = node(
    "Profile",
    *reveal_nodes,
    node(
        "Skeleton",
        *skeleton,
        shape(
            "Avatar ring",
            E("Ellipse", width=48, height=48),
            fill(WHITE),
            x=AV[0],
            y=AV[1],
        ),
    ),
    shape(
        "Band warm",
        E("Rectangle", width=W, height=46, **band_rect),
        fill(BAND),
        y=-HH + 23,
        opacity=0,
        id_=BAND_WARM,
    ),
    shape(
        "Band",
        E("Rectangle", width=W, height=46, **band_rect),
        fill(SKEL_BAND),
        y=-HH + 23,
    ),
    shape("Body", rect(W, H, 16), fill(WHITE), stroke(HAIR, 2)),
    *card_shadows(W, H, 16, 4, 8),
    id_=PULSE,
)

# check badge, pop ring and sparks: hidden until the finale
badge = node(
    "Check badge",
    shape(
        "Check",
        E(
            "PointsPath",
            E("StraightVertex", x=-6.5, y=0.5),
            E("StraightVertex", x=-2, y=5),
            E("StraightVertex", x=7, y=-5),
            isClosed=False,
        ),
        E(
            "Stroke",
            solid(WHITE),
            E("TrimPath", start=0, end=0, id_=TRIM),
            thickness=4,
            cap="round",
            join="round",
        ),
    ),
    shape(
        "Disc",
        E("Ellipse", width=32, height=32),
        E(
            "Fill",
            E(
                "LinearGradient",
                E("GradientStop", colorValue=ORANGE_LIGHT, position=0),
                E("GradientStop", colorValue=ORANGE_DEEP, position=1),
                startX=0,
                startY=-16,
                endX=0,
                endY=16,
            ),
        ),
    ),
    shape("Ring", E("Ellipse", width=40, height=40), fill(WHITE)),
    shape("Glow", E("Ellipse", width=54, height=54), fill("33F86F3C")),
    x=BADGE[0],
    y=BADGE[1],
    scaleX=0,
    scaleY=0,
    id_=BADGE_ID,
)
pop_ring = shape(
    "Pop ring",
    E("Ellipse", width=40, height=40),
    stroke(ORANGE, 2),
    x=BADGE[0],
    y=BADGE[1],
    opacity=0,
    id_=POP_RING,
)
SPARK_DEF = []
spark_shapes = []
colors = [ORANGE, BAND, GOLD, ORANGE_LIGHT, BAND, ORANGE, GOLD, BAND]
for i, sid in enumerate(SPARKS):
    ang = -math.pi / 2 + i * (2 * math.pi / len(SPARKS)) + 0.2
    dist = 44 if i % 2 == 0 else 36
    size = 11 if i % 2 == 0 else 7
    SPARK_DEF.append((sid, ang, dist))
    geom = (
        E(
            "Star",
            width=size + 2,
            height=size + 2,
            points=4,
            innerRadius=0.35,
            cornerRadius=1,
        )
        if i % 2 == 0
        else E("Ellipse", width=size, height=size)
    )
    spark_shapes.append(
        shape(
            f"Spark {i + 1}",
            geom,
            fill(colors[i]),
            x=BADGE[0],
            y=BADGE[1],
            opacity=0,
            id_=sid,
        )
    )

card = node("Card", badge, pop_ring, *spark_shapes, pulse, x=CX, y=CY, id_=CARD)

# ---------------------------------------------------------------- sheets

SW, SH = 46, 58


def sheet_lines(kind, c1, c2):
    """The printed content of a sheet, drawn in c1 (headings) and c2 (text)."""

    def ln(x, y, w, h, c):
        return shape("Text", rect(w, h, h / 2, originX=0), fill(c), x=x, y=y)

    if kind == "CV":
        return [
            ln(-15, -18, 22, 5, c1),
            ln(-15, -8, 30, 3.5, c2),
            ln(-15, -1, 26, 3.5, c2),
            ln(-15, 6, 30, 3.5, c2),
            ln(-15, 13, 18, 3.5, c2),
        ]
    if kind == "LinkedIn":
        return [
            ln(-1, -18, 16, 4.5, c1),
            ln(-1, -11, 11, 3.5, c2),
            ln(-15, 1, 30, 3.5, c2),
            ln(-15, 8, 24, 3.5, c2),
            ln(-15, 16, 14, 5, c1),
        ]
    return [
        ln(-15, -7, 30, 3.5, c2),
        ln(-15, 0, 24, 3.5, c2),
        ln(-15, 7, 28, 3.5, c2),
        ln(-15, 14, 16, 3.5, c2),
    ]


def sheet_node(k):
    kind = SHEETS[k][0]
    nid_, rid, gid = SHEET_IDS[k]
    st = sheet_state(sheet_u(0, k), k)
    extra = []
    if kind == "LinkedIn":
        extra = [
            shape("Photo", E("Ellipse", width=10, height=10), fill(BAND), x=-10, y=-15)
        ]
    if kind == "Notes":
        extra = [
            shape(
                "Band",
                E(
                    "Rectangle",
                    width=SW,
                    height=14,
                    linkCornerRadius=False,
                    cornerRadiusTL=6,
                    cornerRadiusTR=6,
                    cornerRadiusBL=0,
                    cornerRadiusBR=0,
                ),
                fill(CHIP),
                y=-SH / 2 + 7,
            )
        ]
    read = node("Read", *sheet_lines(kind, ORANGE_LIGHT, BAND), opacity=0, id_=rid)
    glow = shape(
        "Scan glow",
        rect(SW + 6, SH + 6, 9),
        stroke("99FFC298", 1.5),
        opacity=0,
        id_=gid,
    )
    return node(
        f"Sheet {kind}",
        glow,
        read,
        *sheet_lines(kind, LINE1, LINE2),
        *extra,
        shape("Paper", rect(SW, SH, 6), fill(WHITE), stroke(HAIR, 1.5)),
        *card_shadows(SW, SH, 6, 2, 4),
        x=st["x"],
        y=st["y"],
        rotation=st["rotation"],
        scaleX=st["scale"],
        scaleY=st["scale"],
        opacity=0,
        id_=nid_,
    )


sheets = [sheet_node(k) for k in range(len(SHEETS))]

# ---------------------------------------------------------------- beam

beam = node(
    "Beam",
    shape(
        "Scan line",
        rect(60, 2.5, 1.25),
        E(
            "Fill",
            E(
                "LinearGradient",
                E("GradientStop", colorValue="00F88F4F", position=0),
                E("GradientStop", colorValue="E6F88F4F", position=0.5),
                E("GradientStop", colorValue="00F88F4F", position=1),
                startX=-30,
                startY=0,
                endX=30,
                endY=0,
            ),
        ),
        opacity=0,
        id_=SCAN,
    ),
    shape(
        "Core",
        rect(2, 170, 1),
        E(
            "Fill",
            E(
                "LinearGradient",
                E("GradientStop", colorValue="00F88F4F", position=0),
                E("GradientStop", colorValue="BFF88F4F", position=0.5),
                E("GradientStop", colorValue="00F88F4F", position=1),
                startX=0,
                startY=-85,
                endX=0,
                endY=85,
            ),
        ),
        opacity=0.6,
        id_=BEAM_CORE,
    ),
    shape(
        "Glow",
        E("Ellipse", width=44, height=44),
        E(
            "Fill",
            E(
                "RadialGradient",
                E("GradientStop", colorValue="80FFC298", position=0),
                E("GradientStop", colorValue="00FFC298", position=1),
                startX=0,
                startY=0,
                endX=22,
                endY=0,
            ),
        ),
        scaleY=4.2,
        opacity=0.6,
        id_=BEAM_GLOW,
    ),
    x=round(XB, 2),
    y=BY,
    id_=BEAM,
)

# ---------------------------------------------------------------- loop


def any_glow(t):
    return max(at(t, k, "glow") for k in range(len(SHEETS)))


def scan_state(t):
    """The scan line sweeps down each sheet while it passes under the beam."""
    best = None
    for k in range(len(SHEETS)):
        u = sheet_u(t, k)
        if u <= 1 and abs(u - 0.5) < 0.2:
            best = (k, u)
    if best is None:
        # park it where the next sweep starts
        k = min(range(len(SHEETS)), key=lambda k: (0.3 - sheet_u(t, k)) % (P / J))
        return SHEETS[k][2] - 26 - BY, 0.0
    k, u = best
    s = smooth((u - 0.38) / 0.24)
    return SHEETS[k][2] - 26 + 52 * s - BY, bump(u, 0.5, 0.14)


loop_objs = []
for k in range(len(SHEETS)):
    nid_, rid, gid = SHEET_IDS[k]
    loop_objs.append(
        keyed(
            nid_,
            {
                "x": sampled(lambda t, k=k: at(t, k, "x"), 0.2),
                "y": sampled(lambda t, k=k: at(t, k, "y"), 0.2),
                "rotation": sampled(lambda t, k=k: at(t, k, "rotation"), 0.002),
                "scaleX": sampled(lambda t, k=k: at(t, k, "scale"), 0.003),
                "scaleY": sampled(lambda t, k=k: at(t, k, "scale"), 0.003),
                "opacity": sampled(lambda t, k=k: at(t, k, "opacity"), 0.012),
            },
        )
    )
    loop_objs.append(
        keyed(rid, {"opacity": sampled(lambda t, k=k: at(t, k, "read"), 0.012)})
    )
    loop_objs.append(
        keyed(gid, {"opacity": sampled(lambda t, k=k: at(t, k, "glow"), 0.012)})
    )

loop_objs.append(
    keyed(BEAM_GLOW, {"opacity": sampled(lambda t: 0.55 + 0.45 * any_glow(t), 0.012)})
)
loop_objs.append(
    keyed(BEAM_CORE, {"opacity": sampled(lambda t: 0.5 + 0.5 * any_glow(t), 0.012)})
)
loop_objs.append(
    keyed(
        SCAN,
        {
            "y": sampled(lambda t: scan_state(t)[0], 0.15),
            "opacity": sampled(lambda t: scan_state(t)[1], 0.012),
        },
    )
)
loop_objs.append(
    keyed(
        CARD,
        {
            "y": sampled(lambda t: CY - 3.5 * math.sin(2 * math.pi * t / P), 0.06),
            "rotation": sampled(
                lambda t: 0.008 * math.sin(2 * math.pi * t / P), 0.0004
            ),
        },
    )
)


def absorb_pulse(t):
    return max(
        bump(sheet_u(t, k), 0.93, 0.1) if sheet_u(t, k) <= 1.05 else 0
        for k in range(len(SHEETS))
    )


loop_objs.append(
    keyed(
        PULSE,
        {
            "scaleX": sampled(lambda t: 1 + 0.016 * absorb_pulse(t), 0.0006),
            "scaleY": sampled(lambda t: 1 + 0.016 * absorb_pulse(t), 0.0006),
        },
    )
)

anim_loop = E(
    "LinearAnimation",
    *loop_objs,
    loopValue="loop",
    duration=P,
    name="Reading",
    id_=A_LOOP,
)

# ---------------------------------------------------------------- finale

OUT_EASE = (0.22, 1, 0.36, 1)
IN_OUT = (0.65, 0, 0.35, 1)
SOFT = (0.33, 0, 0.2, 1)

fin_objs = []
for k in range(len(SHEETS)):
    nid_, rid, gid = SHEET_IDS[k]
    fin_objs.append(
        keyed(
            nid_,
            {
                "x": [kf(0, XE, "hold")],
                "y": [kf(0, YE, "hold")],
                "rotation": [kf(0, 0, "hold")],
                "scaleX": [kf(0, 0.38, "hold")],
                "scaleY": [kf(0, 0.38, "hold")],
                "opacity": [kf(0, 0, "hold")],
            },
        )
    )
    fin_objs.append(keyed(rid, {"opacity": [kf(0, 1, "hold")]}))
    fin_objs.append(keyed(gid, {"opacity": [kf(0, 0, "hold")]}))
fin_objs.append(keyed(BEAM_GLOW, {"opacity": [kf(0, 0, "hold")]}))
fin_objs.append(keyed(BEAM_CORE, {"opacity": [kf(0, 0, "hold")]}))
fin_objs.append(keyed(SCAN, {"opacity": [kf(0, 0, "hold")]}))
fin_objs.append(
    keyed(
        CARD,
        {
            "x": [kf(0, CX, IN_OUT), kf(32, CX_END)],
            "y": [kf(0, CY, IN_OUT), kf(18, CY - 5, IN_OUT), kf(44, CY)],
            "rotation": [kf(0, 0, "hold")],
        },
    )
)
fin_objs.append(
    keyed(
        PULSE,
        {
            "scaleX": [
                kf(0, 1, "hold"),
                kf(24, 1, OUT_EASE),
                kf(36, 1.03, IN_OUT),
                kf(58, 1),
            ],
            "scaleY": [
                kf(0, 1, "hold"),
                kf(24, 1, OUT_EASE),
                kf(36, 1.03, IN_OUT),
                kf(58, 1),
            ],
        },
    )
)
fin_objs.append(
    keyed(
        BADGE_ID,
        {
            "scaleX": [
                kf(0, 0, "hold"),
                kf(24, 0, OUT_EASE),
                kf(38, 1.16, IN_OUT),
                kf(46, 0.95, IN_OUT),
                kf(54, 1),
            ],
            "scaleY": [
                kf(0, 0, "hold"),
                kf(24, 0, OUT_EASE),
                kf(38, 1.16, IN_OUT),
                kf(46, 0.95, IN_OUT),
                kf(54, 1),
            ],
        },
    )
)
fin_objs.append(keyed(TRIM, {"end": [kf(0, 0, "hold"), kf(32, 0, SOFT), kf(48, 1)]}))
fin_objs.append(
    keyed(
        POP_RING,
        {
            "opacity": [kf(0, 0, "hold"), kf(30, 0), kf(33, 0.8, SOFT), kf(58, 0)],
            "scaleX": [kf(0, 0.9, "hold"), kf(30, 0.9, OUT_EASE), kf(58, 1.9)],
            "scaleY": [kf(0, 0.9, "hold"), kf(30, 0.9, OUT_EASE), kf(58, 1.9)],
        },
    )
)
for i, (sid, ang, dist) in enumerate(SPARK_DEF):
    t0 = 32 + (i % 3)
    sx, sy = BADGE[0] + 15 * math.cos(ang), BADGE[1] + 15 * math.sin(ang)
    ex, ey = BADGE[0] + dist * math.cos(ang), BADGE[1] + dist * math.sin(ang)
    fin_objs.append(
        keyed(
            sid,
            {
                "x": [kf(0, sx, "hold"), kf(t0, sx, OUT_EASE), kf(t0 + 30, ex)],
                "y": [kf(0, sy, "hold"), kf(t0, sy, OUT_EASE), kf(t0 + 30, ey)],
                "opacity": [
                    kf(0, 0, "hold"),
                    kf(t0, 0),
                    kf(t0 + 4, 1, "hold"),
                    kf(t0 + 14, 1, SOFT),
                    kf(t0 + 32, 0),
                ],
                "scaleX": [kf(0, 1, "hold"), kf(t0, 1, SOFT), kf(t0 + 32, 0.35)],
                "scaleY": [kf(0, 1, "hold"), kf(t0, 1, SOFT), kf(t0 + 32, 0.35)],
                "rotation": [
                    kf(0, 0, "hold"),
                    kf(t0, 0, SOFT),
                    kf(t0 + 32, 0.9 if i % 2 == 0 else 0),
                ],
            },
        )
    )

anim_fin = E(
    "LinearAnimation",
    *fin_objs,
    loopValue="oneShot",
    duration=FIN,
    name="Finish",
    id_=A_FIN,
)

# ---------------------------------------------------------------- fill poses

pose_elems = [
    E(
        "LinearAnimation",
        *full_pose_keys(p),
        loopValue="oneShot",
        duration=1,
        name=f"Profile {fmt(p)}",
        id_=aid,
    )
    for p, aid in pose_anims
]
# the finale holds the pose of a full profile, whatever progress had reached
A_FULL = pose_anims[-1][1]

# ---------------------------------------------------------------- state machine


def on_finish():
    return E(
        "TransitionViewModelCondition",
        E(
            "TransitionPropertyViewModelComparator",
            E(
                "BindablePropertyTrigger",
                E("DataBindContext", sourcePathIds=f"{VM}-{P_FIN}", propertyKey=686),
            ),
        ),
        E("TransitionValueTriggerComparator"),
    )


sm = E(
    "StateMachine",
    E(
        "StateMachineLayer",
        E("AnyState", x=200, y=-120),
        E("ExitState", x=600, y=-120),
        E("EntryState", E("StateTransition", stateToId=S_BLEND), x=0, y=0),
        E(
            "BlendState1DViewModel",
            E(
                "BindablePropertyNumber",
                E(
                    "DataBindContext",
                    sourcePathIds=f"{VM}-{P_PROG}",
                    propertyKey=636,
                    converterId=SMOOTH,
                ),
            ),
            *[E("BlendAnimation1D", animationId=aid, value=p) for p, aid in pose_anims],
            E(
                "BlendStateTransition",
                E("CubicEaseInterpolator", x1=0.33, y1=0, x2=0.2, y2=1),
                on_finish(),
                stateToId=S_FULL,
                duration=500,
                interpolationType="cubic",
            ),
            x=200,
            y=0,
            id_=S_BLEND,
        ),
        E("AnimationState", animationId=A_FULL, x=400, y=0, id_=S_FULL),
        name="Profile",
    ),
    E(
        "StateMachineLayer",
        E("AnyState", x=200, y=-120),
        E("ExitState", x=600, y=-120),
        E("EntryState", E("StateTransition", stateToId=S_LOOP), x=0, y=0),
        E(
            "AnimationState",
            E(
                "StateTransition",
                E("CubicEaseInterpolator", x1=0.33, y1=0, x2=0.2, y2=1),
                on_finish(),
                stateToId=S_FIN,
                duration=320,
                interpolationType="cubic",
            ),
            animationId=A_LOOP,
            x=200,
            y=0,
            id_=S_LOOP,
        ),
        E("AnimationState", animationId=A_FIN, x=400, y=0, id_=S_FIN),
        name="Motion",
    ),
    name="Building",
    id_=SM,
)

artboard = E(
    "Artboard",
    E("LayoutComponentStyle", name="Building style", id_=STYLE),
    sm,
    beam,
    *sheets,
    card,
    anim_loop,
    anim_fin,
    *pose_elems,
    defaultStateMachineId=SM,
    viewModelId=VM,
    viewModelInstanceId=VMI,
    styleId=STYLE,
    width=480,
    height=300,
    x=0,
    y=0,
    name="Building",
    id_=ART,
)

converter = E(
    "DataConverterInterpolator",
    E("CubicEaseInterpolator", x1=0.25, y1=0.1, x2=0.25, y2=1),
    interpolationType="cubic",
    duration=0.9,
    name="Ease progress",
    id_=SMOOTH,
)

vm = E(
    "ViewModel",
    E("ViewModelPropertyNumber", name="progress", id_=P_PROG),
    E("ViewModelPropertyTrigger", name="finish", id_=P_FIN),
    E(
        "ViewModelInstance",
        E("ViewModelInstanceNumber", propertyValue=0, viewModelPropertyId=P_PROG),
        E("ViewModelInstanceTrigger", viewModelPropertyId=P_FIN),
        exports=True,
        name="Default",
        id_=VMI,
    ),
    defaultInstanceId=VMI,
    name="Building",
    id_=VM,
)

doc = E("Rive", artboard, converter, vm, version=1, kind="fragment")
with open(OUT, "w") as f:
    f.write(doc.render() + "\n")
print(f"beam x {XB:.1f}, poses {len(BREAKS)}", file=sys.stderr)
