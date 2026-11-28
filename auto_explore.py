# -*- coding: utf-8 -*-
"""
auto_explore.py —— 无人值守的长期探索引擎
==========================================
解决的问题：用户不想每天催办。这个脚本由 GitHub Actions 每天自动跑一次，
无需任何 LLM、无需 token、无需人说话。

每轮做三件事：
  1. 随机生成 N 条全新假设（随机特征组合 × 随机分桶数 × 随机选码方式）
     指纹去重，只测登记簿里没有的
  2. 每条跑六道闸，结果写进 auto_registry.json
  3. 导出 research_status.json 供可视化页面读取

跑一年 = 365 × 6 ≈ 2200 条假设。这是真正的长期训练，不靠对话。
"""
import json, io, math, itertools, random, os, hashlib, datetime, sys, time
from collections import defaultdict, Counter

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "meeting3", "draws_8825_full.json")
REG = os.path.join(BASE, "meeting3", "auto_registry.json")
SHADOW = os.path.join(BASE, "meeting3", "auto_shadow.json")
STATUS = os.path.join(BASE, "meeting3", "research_status.json")

BASE_RATE = 0.339943
BASE_NOTES = 56
EFFECT_MIN = 1.5
WARM = 500

FEATS = {
    "span":   lambda n: max(n) - min(n),
    "sum":    lambda n: sum(n),
    "big":    lambda n: sum(1 for v in n if v >= 5),
    "odd":    lambda n: sum(1 for v in n if v % 2 == 1),
    "prime":  lambda n: sum(1 for v in n if v in (2, 3, 5, 7)),
    "form":   lambda n: len(set(n)),
    "pai":    lambda n: n[0],
    "shi":    lambda n: n[1],
    "ge":     lambda n: n[2],
    "tail":   lambda n: sum(n) % 10,
    "road":   lambda n: sum(1 for v in n if v % 3 == 0),
    "consec": lambda n: 1 if any(b - a == 1 for a, b in zip(sorted(set(n)), sorted(set(n))[1:])) else 0,
    "rep":    lambda n: 3 - len(set(n)),
    "mall":   lambda n: max(n),
    "mill":   lambda n: min(n),
    "mid":    lambda n: sorted(n)[1],
}

ALL8 = [tuple(c) for c in itertools.combinations(range(10), 8)]
ALL7 = [tuple(c) for c in itertools.combinations(range(10), 7)]
FORMS = [tuple(c) for c in itertools.combinations(range(10), 3)]
S8T = (0, 1, 2, 4, 5, 6, 8, 9)


def load():
    d = json.load(io.open(DATA, encoding="utf-8"))
    for x in d:
        x["n"] = sorted(x["nums"])
    return d


def z6(n):
    return len(set(n)) == 3


def make_rule(shape, fa, fb, mode_name):
    """按 shape 造一个分桶函数"""
    if shape == "single":
        return lambda n: FEATS[fa](n)
    if shape == "pair":
        return lambda n: (FEATS[fa](n), FEATS[fb](n))
    if shape == "diff":
        return lambda n: FEATS[fa](n) - FEATS[fb](n)
    if shape == "gt":
        return lambda n: 1 if FEATS[fa](n) > FEATS[fb](n) else 0
    return lambda n: FEATS[fa](n)


def evaluate(d, keyfn, pick, name):
    """六道闸"""
    N = len(d)
    stat = defaultdict(Counter)
    h = t = notes = 0
    for i, x in enumerate(d):
        if i >= WARM:
            k = keyfn(d[i - 1]["n"])
            c = stat[k]
            t += 1
            if pick == "8":
                cs = max(ALL8, key=lambda z: (c[z], -sum(z))) if c else S8T
                notes += 56
                if z6(x["n"]) and set(x["n"]) <= set(cs):
                    h += 1
            elif pick == "7":
                cs = max(ALL7, key=lambda z: (c[z], -sum(z))) if c else (0, 1, 4, 5, 6, 8, 9)
                notes += 35
                if z6(x["n"]) and set(x["n"]) <= set(cs):
                    h += 1
            else:
                top = set(z for z, _ in c.most_common(56)) if c else set(FORMS[:56])
                notes += 56
                if z6(x["n"]) and tuple(x["n"]) in top:
                    h += 1
        if z6(x["n"]):
            k = keyfn(x["n"])
            if pick == "8":
                for cs in ALL8:
                    if set(x["n"]) <= set(cs):
                        stat[k][cs] += 1
            elif pick == "7":
                for cs in ALL7:
                    if set(x["n"]) <= set(cs):
                        stat[k][cs] += 1
            else:
                stat[k][tuple(x["n"])] += 1

    if t == 0:
        return None
    rate = h / t
    avg = notes / t
    delta = 100 * (rate - BASE_RATE)
    eff = rate / avg if avg else 0
    base_eff = BASE_RATE / BASE_NOTES

    g1 = True
    g3 = eff >= base_eff - 1e-9 and rate >= BASE_RATE
    g6 = delta >= EFFECT_MIN

    half = N // 2
    def run_seg(seg):
        st = defaultdict(Counter)
        hh = tt = 0
        for i, x in enumerate(seg):
            if i >= min(WARM, len(seg) // 3):
                k = keyfn(seg[i - 1]["n"])
                c = st[k]
                if pick == "8":
                    cs = max(ALL8, key=lambda z: (c[z], -sum(z))) if c else S8T
                    tt += 1
                    if z6(x["n"]) and set(x["n"]) <= set(cs):
                        hh += 1
                elif pick == "7":
                    cs = max(ALL7, key=lambda z: (c[z], -sum(z))) if c else (0, 1, 4, 5, 6, 8, 9)
                    tt += 1
                    if z6(x["n"]) and set(x["n"]) <= set(cs):
                        hh += 1
                else:
                    top = set(z for z, _ in c.most_common(56)) if c else set(FORMS[:56])
                    tt += 1
                    if z6(x["n"]) and tuple(x["n"]) in top:
                        hh += 1
            if z6(x["n"]):
                k = keyfn(x["n"])
                if pick == "8":
                    for cs in ALL8:
                        if set(x["n"]) <= set(cs):
                            st[k][cs] += 1
                elif pick == "7":
                    for cs in ALL7:
                        if set(x["n"]) <= set(cs):
                            st[k][cs] += 1
                else:
                    st[k][tuple(x["n"])] += 1
        return hh / tt if tt else 0

    r1 = run_seg(d[:half])
    r2 = run_seg(d[half:])
    g4 = r1 > BASE_RATE and r2 > BASE_RATE

    ys = defaultdict(list)
    for x in d:
        ys[int(str(x["issue"])[:4])].append(x)
    wins = tot = 0
    for y, seg in ys.items():
        if len(seg) < 300:
            continue
        rr = run_seg(seg)
        if rr <= 0:
            continue
        tot += 1
        if rr >= BASE_RATE:
            wins += 1
    g5 = tot > 0 and wins >= 0.72 * tot

    g2 = False
    if g1 and g3 and g4 and g5 and g6:
        rnd = random.Random(hash(name) & 0xFFFF)
        sims = []
        for _ in range(60):
            st = defaultdict(Counter)
            hh = tt = 0
            for i, x in enumerate(d):
                if i >= WARM:
                    k = keyfn(d[i - 1]["n"])
                    c = st[k]
                    cs = max(ALL8, key=lambda z: (c[z], -sum(z))) if c else S8T
                    tt += 1
                    if z6(x["n"]) and set(x["n"]) <= set(cs):
                        hh += 1
                if z6(x["n"]):
                    k = keyfn(x["n"])
                    for cs in ALL8:
                        if set(x["n"]) <= set(cs):
                            st[k][cs] += 1
            sims.append(hh / tt if tt else 0)
        sims.sort()
        g2 = rate > sims[int(0.95 * len(sims))]

    return {
        "name": name, "rate": round(100 * rate, 4), "delta": round(delta, 3),
        "avg_notes": round(avg, 1), "eff": round(eff, 6),
        "half": [round(100 * r1, 2), round(100 * r2, 2)],
        "year_wins": f"{wins}/{tot}",
        "gates": {"G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6},
        "pass": all([g1, g2, g3, g4, g5, g6]),
        "shadow": g1 and g3 and g6 and not g2,
    }


def gen_candidates(n, seed):
    """随机生成 n 条新假设"""
    rnd = random.Random(seed)
    keys = list(FEATS)
    out = []
    for _ in range(n):
        shape = rnd.choice(["single", "pair", "diff", "gt"])
        fa = rnd.choice(keys)
        fb = rnd.choice([k for k in keys if k != fa])
        pick = rnd.choice(["8", "8", "7", "56"])
        name = f"X_{shape}_{fa}_{fb}_{pick}" if shape != "single" else f"X_single_{fa}_{pick}"
        out.append((name, shape, fa, fb, pick))
    return out


def export_status(d):
    reg = json.load(io.open(REG, encoding="utf-8")) if os.path.exists(REG) else {}
    sh = json.load(io.open(SHADOW, encoding="utf-8")) if os.path.exists(SHADOW) else {}
    vals = list(reg.values())
    if not vals:
        io.open(STATUS, "w", encoding="utf-8").write(json.dumps({"empty": True}))
        return
    deltas = sorted(v["delta"] for v in vals)
    top = sorted(vals, key=lambda v: -v["delta"])[:15]
    by_fam = Counter(v["name"].split("_")[1] if v["name"].startswith("X_") else v["name"].split("_")[0]
                     for v in vals)
    status = {
        "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": len(vals),
        "passed": sum(1 for v in vals if v.get("pass")),
        "shadow": len(sh),
        "median_delta": round(deltas[len(deltas) // 2], 2),
        "max_delta": round(deltas[-1], 2),
        "over_g6": sum(1 for v in vals if v["delta"] >= EFFECT_MIN),
        "top": [{"name": v["name"], "rate": v["rate"], "delta": v["delta"],
                 "half": v["half"], "year_wins": v.get("year_wins", ""),
                 "notes": v["avg_notes"]} for v in top],
        "families": dict(by_fam.most_common(12)),
        "baseline": 33.9943,
    }
    io.open(STATUS, "w", encoding="utf-8").write(json.dumps(status, ensure_ascii=False, indent=1))
    print("状态已导出:", STATUS)


def main():
    if "--status-only" in sys.argv:
        export_status(None)
        return
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 6
    reg = json.load(io.open(REG, encoding="utf-8")) if os.path.exists(REG) else {}
    shadow = json.load(io.open(SHADOW, encoding="utf-8")) if os.path.exists(SHADOW) else {}
    d = load()

    seed = int(datetime.date.today().strftime("%Y%m%d"))
    cands = gen_candidates(n, seed)
    print("生成 %d 条候选（seed=%d），登记簿已有 %d 条" % (len(cands), seed, len(reg)))

    new = 0
    for name, shape, fa, fb, pick in cands:
        fp = hashlib.md5(name.encode()).hexdigest()[:12]
        if fp in reg:
            print("  跳过（已测）%s" % name)
            continue
        keyfn = make_rule(shape, fa, fb, pick)
        try:
            r = evaluate(d, keyfn, pick, name)
        except Exception as e:
            print("  %s ERROR %s" % (name, e))
            continue
        if not r:
            continue
        r["fp"] = fp
        r["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
        reg[fp] = r
        new += 1
        tag = "PASS" if r["pass"] else ("影子" if r["shadow"] else "----")
        print("  %-34s %8.3f%%  Δ%+6.2f  注%5.1f  %s" % (name, r["rate"], r["delta"], r["avg_notes"], tag))
        if r["shadow"]:
            shadow[fp] = {"name": name, "start_issue": d[-1]["issue"],
                          "start_delta": r["delta"], "hits": 0, "periods": 0}

    io.open(REG, "w", encoding="utf-8").write(json.dumps(reg, ensure_ascii=False, indent=1))
    io.open(SHADOW, "w", encoding="utf-8").write(json.dumps(shadow, ensure_ascii=False, indent=1))
    print()
    print("本轮新增 %d 条，登记簿共 %d 条" % (new, len(reg)))
    export_status(d)


if __name__ == "__main__":
    main()
