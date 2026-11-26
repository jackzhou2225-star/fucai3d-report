# -*- coding: utf-8 -*-
"""
把新开奖期追加进 index.html 的逐期台账（const P=[...]），并同步更新台账头部统计。
字段口径与原始 302 期完全一致（见 build_v2.py 的 periods.append）。
"""
import json, re, io

SEL = "01245689"
SET = set(int(c) for c in SEL)
KILL = {3, 7}
PRIME = {2, 3, 5, 7}


def _roll(hist, t, win):
    """第 t 期开奖前，前 win 期各数字出现情况 -> {d: [cnt, n, rate]}"""
    lo = max(0, t - win)
    n = t - lo
    out = {}
    for d in range(10):
        c = sum(1 for u in range(lo, t) if d in set(hist[u][1]))
        out[str(d)] = [c, n, (c / n if n > 0 else 0.0)]
    return out


def _gaps(hist, t):
    out = {}
    for d in range(10):
        g = 0
        for u in range(t - 1, -1, -1):
            if d in set(hist[u][1]):
                break
            g += 1
        out[str(d)] = g
    return out


def _indicators(hist, t, dd):
    a, b, c = dd
    sm = sum(dd); tl = sm % 10; sp = max(dd) - min(dd)
    big = sum(1 for x in dd if x >= 5); odd = sum(1 for x in dd if x % 2 == 1)
    pz = sum(1 for x in dd if x in PRIME)
    r0 = sum(1 for x in dd if x % 3 == 0)
    r1 = sum(1 for x in dd if x % 3 == 1)
    r2 = sum(1 for x in dd if x % 3 == 2)
    szL = sum(1 for x in dd if x >= 7)
    szM = sum(1 for x in dd if 3 <= x <= 6)
    szS = sum(1 for x in dd if x <= 2)
    sset = set(dd)
    zt = '豹子' if len(sset) == 1 else ('组三' if len(sset) == 2 else '组六')
    sd = sorted(sset)
    if len(sset) == 3:
        if (sd[1] - sd[0] == 1 and sd[2] - sd[1] == 1) or (sd[0] == 0 and sd[2] == 9):
            xx = '全顺'
        elif (sd[1] - sd[0] == 1) or (sd[2] - sd[1] == 1):
            xx = '半顺'
        else:
            xx = '杂六'
    else:
        xx = '—'
    if a == b == c:
        tr = '平行'
    elif a <= b <= c:
        tr = '上升'
    elif a >= b >= c:
        tr = '下降'
    elif b > a and b > c:
        tr = '凸起'
    elif b < a and b < c:
        tr = '凹下'
    else:
        tr = '其他'
    lo = max(0, t - 60); n = t - lo
    sm60 = sum(sum(hist[u][1]) for u in range(lo, t)) / n if n else 0
    sp60 = sum(max(hist[u][1]) - min(hist[u][1]) for u in range(lo, t)) / n if n else 0
    z6_60 = sum(1 for u in range(lo, t) if len(set(hist[u][1])) == 3) / n if n else 0
    z3_60 = sum(1 for u in range(lo, t) if len(set(hist[u][1])) == 2) / n if n else 0
    return dict(sum=sm, sum60=round(sm60, 4), tail=tl, span=sp, span60=round(sp60, 4),
                bs=f'{big}:{3 - big}', oe=f'{odd}:{3 - odd}', pn=f'{pz}:{3 - pz}',
                lu=f'{r0}:{r1}:{r2}', size=f'{szL}:{szM}:{szS}',
                zt=zt, xx=xx, tr=tr,
                z6_60=round(z6_60, 6), z3_60=round(z3_60, 6), bs_60=0)


def _hz(dd, S):
    return len(set(dd)) == 3 and set(dd).issubset(S)


def build_record(hist, t, issue, digits):
    dd = list(digits)
    prev = hist[t - 1][1] if t > 0 else ()
    sv = sorted(set(dd))
    h_all = set(dd).issubset(SET)
    if t > 0:
        pmax, pmin = max(prev), min(prev)
        p01 = (0 in prev) or (1 in prev)
        pshape = '组三' if len(set(prev)) == 2 else ('豹子' if len(set(prev)) == 1 else '组六')
        previss = hist[t - 1][0]
        prev_s = ''.join(map(str, prev))
    else:
        pmax = pmin = '—'; p01 = None; pshape = '—'; previss = 0; prev_s = '—'
    return dict(
        i=issue,
        prev=prev_s, previss=previss,
        pmax=pmax, pmin=pmin, p01=p01, pshape=pshape,
        draw=''.join(map(str, dd)),
        shape=('组三' if len(set(dd)) == 2 else ('豹子' if len(set(dd)) == 1 else '组六')),
        cov=[{'d': d, 'in': d in SET} for d in dd],
        in_cnt=sum(1 for d in dd if d in SET),
        killed=sorted({d for d in dd if d in KILL}),
        has2=2 in set(dd),
        adj=any(sv[k + 1] - sv[k] == 1 for k in range(len(sv) - 1)),
        hit=h_all, z6=_hz(dd, SET), cum=0, sel=SEL,
        r60=_roll(hist, t, 60),
        r60full=_roll(hist, t, t if t > 0 else 1),
        gap=_gaps(hist, t),
        s7hit=_hz(dd, {'0', '2', '4', '5', '6', '8', '9'}),
        s6hit=_hz(dd, {'0', '2', '4', '5', '6', '9'}),
        ind=_indicators(hist, t, dd),
        sig=[], yu='', yug=False, yuhit=None, zone='AI',
    )


def append_to_html(html_path, draws):
    """draws: [{"issue": int, "nums": [int,int,int]}]，按issue升序。返回 (新增条数, 新总数)"""
    with io.open(html_path, "r", encoding="utf-8") as f:
        doc = f.read()

    m = re.search(r"const P=(\[.*?\]);", doc, re.S)
    if not m:
        raise RuntimeError("index.html 中找不到 const P=[...]")
    P = json.loads(m.group(1))
    last_issue = P[-1]["i"]

    # 历史：已有个股的 draw 字符串（保持原始顺序）
    hist = [(p["i"], tuple(int(ch) for ch in p["draw"])) for p in P]
    cum = sum(1 for p in P if p["hit"])

    added = []
    for d in draws:
        if d["issue"] <= last_issue:
            continue
        t = len(hist)
        rec = build_record(hist, t, d["issue"], d["nums"])
        cum += 1 if rec["hit"] else 0
        rec["cum"] = cum
        hist.append((d["issue"], tuple(d["nums"])))
        P.append(rec)
        added.append(d["issue"])

    if not added:
        return 0, len(P)

    new_json = json.dumps(P, ensure_ascii=False, separators=(",", ":"))
    doc = doc[:m.start(1)] + new_json + doc[m.end(1):]

    # ---- 同步台账头部统计 ----
    total = len(P)
    z6 = sum(1 for p in P if p["z6"])
    allhit = sum(1 for p in P if p["hit"])
    z6_rate = 100.0 * z6 / total
    first_issue = P[0]["i"]
    last_new = P[-1]["i"]

    # 最长连亏（按真兑奖 z6 口径）
    ml = cur = 0
    for p in P:
        if p["z6"]:
            cur = 0
        else:
            cur += 1
            ml = max(ml, cur)

    # model-declare 区块
    doc = re.sub(r'(<div><span>实测命中</span><b[^>]*>)[^<]*(</b></div>)',
                 lambda mm: mm.group(1) + f'{z6} / {total} = {z6_rate:.2f}%' + mm.group(2), doc, count=1)
    doc = re.sub(r'统计只认 <b>\d+ / \d+ = [\d.]+%</b>',
                 f'统计只认 <b>{z6} / {total} = {z6_rate:.2f}%</b>', doc, count=1)

    # loghero 统计条
    def _fix_stat(mo):
        body = mo.group(0)
        body = re.sub(r'<b>\d+</b><span>真兑奖期数</span>', f'<b>{z6}</b><span>真兑奖期数</span>', body)
        body = re.sub(r'<b>[\d.]+%</b><span>命中率</span>', f'<b>{z6_rate:.1f}%</b><span>命中率</span>', body)
        body = re.sub(r'<b>[\d.]+%</b><span>真能兑奖</span>', f'<b>{z6_rate:.1f}%</b><span>真能兑奖</span>', body)
        body = re.sub(r'<b>\d+</b><span>最长连亏</span>', f'<b>{ml}</b><span>最长连亏</span>', body)
        return body
    doc = re.sub(r'<div class="stats">.*?</div>\s*</div>', _fix_stat, doc, count=1, flags=re.S)

    # 台账标题与数据区间（正文分析仍冻结在 302 期基准，避免与历史论证矛盾）
    doc = re.sub(r'逐期台账 · \d+ 期(（[^）]*）)?',
                 f'逐期台账 · {total} 期（新开奖自动追加，分析基准仍为 302 期）', doc, count=1)
    doc = re.sub(r'数据区间 <b>\d+–\d+</b>', f'数据区间 <b>{first_issue}–{last_new}</b>', doc, count=1)
    doc = re.sub(r'全部 \d+ 期', f'全部 {total} 期', doc, count=1)

    # 首页大标题 + 副标题 + 页脚（跟着台账实际期数走）
    doc = re.sub(r'<br>\d+ 期逐期论证</h1>', f'<br>{total} 期逐期论证</h1>', doc, count=1)
    doc = re.sub(r'数据区间 (\d+–\d+)　·　共 \d+ 期', f'数据区间 {first_issue}–{last_new}　·　共 {total} 期', doc, count=1)
    doc = re.sub(r'数据区间 <b>\d+–\d+</b>　·　共 <b>\d+ 期</b>',
                 f'数据区间 <b>{first_issue}–{last_new}</b>　·　共 <b>{total} 期</b>', doc, count=1)

    # 首屏 KPI 卡（台账真实累计）
    doc = re.sub(r'<div class="e">\d+ / \d+ 期 · 随机 33\.6%</div>',
                 f'<div class="e">{z6} / {total} 期 · 随机 33.6%</div>', doc, count=1)
    doc = re.sub(r'<div class="e">\d+ / \d+ 期 · 组三豹子不算中</div>',
                 f'<div class="e">{z6} / {total} 期 · 组三豹子不算中</div>', doc, count=1)

    # 台账底部口径行（活数据）
    doc = re.sub(r'统计口径：组六可兑奖（组三、豹子不计入）· 真兑奖 \d+/\d+ = [\d.]+%',
                 f'统计口径：组六可兑奖（组三、豹子不计入）· 台账内真兑奖 {z6}/{total} = {z6_rate:.2f}%',
                 doc, count=1)

    with io.open(html_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return len(added), total
