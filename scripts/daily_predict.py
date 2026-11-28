# -*- coding: utf-8 -*-
"""
1126 算法每日预测（GitHub Actions 版，在仓库内运行）
只负责：抓福彩官网开奖 → 算当日命中与次日预测 → 更新仓库根目录 index.html 的每日预测区块。
提交与推送由 workflow 里的 git 完成，本脚本不含任何令牌。
"""
import json, re, ssl, urllib.request, datetime, io
from itertools import combinations

HTML_PATH = "index.html"
SEL = "01245689"
SET = set(int(c) for c in SEL)
KILL = "3、7"
CTX = ssl.create_default_context()

# ── 出票拆单：14 张 4 码小复式 = 1126 的 56 注，零重复零遗漏（2026-11-28 全量 8825 期复核）──
# 拆单理由：8 码复式整张票只要 56 注里 1 注被单注限号，官方规则是整张不出票；
# 拆成 14 张后，被限只损 1 张 8 元，不会整张 112 元废掉。
TICKETS = ["0124", "0156", "0189", "0258", "0269", "0459", "0468",
           "1259", "1268", "1458", "1469", "2456", "2489", "5689"]

# ── 长期校准常量（8825 期全历史 2002-2026，2026-11-28 复核锁定）──
# 全期口径实测覆盖率 = 命中数/全部期数（组三豹子算未中）：3000/8825
LONG_COV = 0.339943
# 组六占比：6380/8825
Z6_RATIO = 0.722946
# 各数字长期出现率（8825 期全期口径）
LONG_RATE = {0: 0.271275, 1: 0.273314, 2: 0.260510, 3: 0.270822,
             4: 0.272295, 5: 0.267762, 6: 0.271048, 7: 0.272295,
             8: 0.279547, 9: 0.273654}
# 近 300 期（截至 2026317）的出现率，锁定值——页面「近300期」列用它，
# 因为生产环境 data.js 台账期数会变，现算会跟着漂。
RATE300 = {0: 0.256667, 1: 0.270000, 2: 0.343333, 3: 0.230000,
           4: 0.266667, 5: 0.253333, 6: 0.270000, 7: 0.223333,
           8: 0.313333, 9: 0.283333}

# 俞式阶梯出票（8825 期全样本实测）：(档位, 单期命中率, 平均几期中一次, 用到多少期)
YU_LADDER = [
    ("7 码起步（0145689）", "21.52%", "4.6 期", "3142 期"),
    ("没中升 8 码（01245689）", "27.00%", "3.7 期", "2465 期"),
    ("再没中升 9 码（012456789）", "25.60%", "3.9 期", "3218 期"),
]

# 剔号代价表（8825 期实测：从 8 码剔掉该数字后 7 码的命中率与损失期数）
KILL_COST = {
    2: (1919, 1081), 4: (1892, 1108), 0: (1889, 1111), 1: (1873, 1127),
    5: (1873, 1127), 6: (1872, 1128), 8: (1845, 1155), 9: (1837, 1163),
}
# 四种「换号规律」的严格 walk-forward 实测（7 码 35 注，基准 = 固定剔2 的 21.7450%）
KILL_RULES = [
    ("近 100 期剔最冷", 20.9971, "W=30/50/200/300 分别 21.3985/21.2308/20.8928/20.6569%，越长越差"),
    ("按上期跨度剔", 20.4782, "跨度 5 档各挑最低数字 = 3/2/9/7/1，五档五个不同答案"),
    ("按遗漏剔（越久越出）", 21.0789, "遗漏 5 档下期出现率 27.11/26.98/27.30/27.81/27.56%，不单调"),
    ("按本月组三缺口剔", 21.8244, "缺口 5 档下期组三率 z 全 <1.6；五档建议数字 1/7/4/2/5 不一致"),
]

# 7/6/5 码缩码阶梯的长期校准（8825 期实测，2026-11-28 锁定）
# 剔码顺序 = 8 码内长期出现率由冷到热：2(26.05) → 5(26.78) → 6(27.10)
LADDER_LONG = {7: 0.217450, 6: 0.122833, 5: 0.061756}
# 各档「3 期内至少中一次」与「最长连亏」（8825 期全历史实测，2026-11-28 锁定）
# 注意：不能用 data.js 的台账（仅 317 期）现算，否则会把 317 期的结果标成全历史。
LADDER_ROLL3 = {8: 70.9396, 7: 51.4224, 6: 32.4153, 5: 17.4770}
LADDER_STREAK = {8: 25, 7: 32, 6: 61, 5: 85}
# 平均多久中一次（8825 期 ÷ 命中数）
LADDER_GAP = {8: 2.9, 7: 4.6, 6: 8.1, 5: 16.2}
# 月度「组三+豹子」长期基准（300 个自然月，8825 期）
MONTH_NON6_MEAN = 8.15

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36")


def _get(url, referer=None, timeout=30, xhr=True):
    h = {"User-Agent": UA, "Accept": "*/*",
         "Accept-Language": "zh-CN,zh;q=0.9", "Connection": "close"}
    if referer:
        h["Referer"] = referer
    if xhr:
        h["X-Requested-With"] = "XMLHttpRequest"
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read().decode("utf-8", errors="ignore")


def fetch_latest_api():
    """免注册接口，直接给最新一期。境外可达性最好，用来做"新鲜度锚点"。
    多个 endpoint + 重试，任一成功即返回。"""
    urls = [
        "https://cn.apihz.cn/api/caipiao/fucai3d.php?id=88888888&key=88888888",
        "https://api.apihz.cn/api/caipiao/fucai3d.php?id=88888888&key=88888888",
        "https://cn.apihz.cn/api/caipiao/fucai3d.php?id=88888888&key=88888888&qh=",
    ]
    for attempt in range(3):
        for u in urls:
            try:
                d = json.loads(_get(u, timeout=20, xhr=False))
                if d.get("code") == 200 and d.get("qihao"):
                    nums = [int(c) for c in str(d["number"]).replace("|", " ").split()
                            if c.isdigit()]
                    if len(nums) == 3:
                        return {"issue": int(d["qihao"]), "nums": sorted(nums)}
            except Exception as e:
                print(f"APIHZ_TRY_FAIL: {type(e).__name__} {str(e)[:60]}")
        if attempt < 2:
            import time
            time.sleep(5)
    return None


def _parse_generic(text):
    """解析中彩网/福彩官网两种结构，返回 [{issue,nums}]"""
    if text.startswith("jQuery"):
        text = text[text.find("(") + 1:text.rfind(")")]
    data = json.loads(text)
    out = []
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        for x in data["data"]:                       # 中彩网
            nums = [int(c) for c in str(x.get("frontWinningNum", "")).split()
                    if c.isdigit()]
            if len(nums) == 3:
                out.append({"issue": int(x["issue"]), "nums": sorted(nums)})
    else:
        rows = (data.get("result") if isinstance(data, dict) else data) or []
        for x in rows:                               # 福彩官网
            nums = [int(c) for c in str(x.get("red", "")).split(",")
                    if c.strip().isdigit()]
            if len(nums) == 3:
                out.append({"issue": int(str(x["code"])), "nums": sorted(nums)})
    out.sort(key=lambda d: d["issue"])
    return out


def fetch_draws(count=100):
    """按可靠性依次尝试多源；最后用 freshness 接口补齐最新一期（镜像常滞后一天）。"""
    today = datetime.date.today()
    start = (today - datetime.timedelta(days=180)).isoformat()
    zhcw = ("https://jc.zhcw.com/port/client_json.php?callback=jQuery1"
            f"&transactionType=10001001&lotteryId=2&issueCount={count}"
            f"&startDate={start}&endDate={today.isoformat()}"
            "&type=2&pageNum=1&pageSize=200")
    zhcw_daily = ("https://jc.zhcw.com/port/client_json.php?callback=jQuery1"
                  "&transactionType=10001005&lotteryId=3&count=30")
    cwl = ("http://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice"
           f"?name=3d&issueCount={count}")
    cwl_https = ("https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice"
                 f"?name=3d&issueCount={count}")
    mirror = ("https://raw.githubusercontent.com/wenjinliuu/lottery-data-repo"
              "/main/public_data/v2/draws/fc3d.json")

    errors = []
    best = []

    # A. 中彩网历史（最全）
    for attempt in range(3):
        try:
            out = _parse_generic(_get(zhcw, "https://www.zhcw.com/kjxx/3d/"))
            if len(out) >= 30:
                print(f"SOURCE_OK: zhcw history rows={len(out)}")
                best = out
                break
            errors.append(f"zhcw rows={len(out)}")
        except Exception as e:
            errors.append(f"zhcw#{attempt}: {type(e).__name__} {e}")
            if attempt < 2:
                import time
                time.sleep(4)

    # B. 福彩官网（境内直连，境外常 403；http / https 都试）
    if not best:
        for u, tag in ((cwl_https, "cwl-https"), (cwl, "cwl-http")):
            try:
                out = _parse_generic(_get(u, "http://www.cwl.gov.cn/kjxx/3d/kjgg/"))
                if len(out) >= 30:
                    print(f"SOURCE_OK: {tag} rows={len(out)}")
                    best = out
                    break
                errors.append(f"{tag} rows={len(out)}")
            except Exception as e:
                errors.append(f"{tag}: {type(e).__name__} {e}")

    # C. 中彩网"今日各彩种"（只要最新几条）
    if not best:
        try:
            txt = _get(zhcw_daily, "https://www.zhcw.com/kjxx/3d/")
            if txt.startswith("jQuery"):
                txt = txt[txt.find("(") + 1:txt.rfind(")")]
            d = json.loads(txt)
            for x in d.get("lotteryResults", []):
                if str(x.get("lotteryId")) == "2":
                    nums = [int(c) for c in str(x.get("frontWinningNum", "")).split()
                            if c.isdigit()]
                    if len(nums) == 3:
                        best.append({"issue": int(x["issue"]), "nums": sorted(nums)})
            if best:
                print(f"SOURCE_OK: zhcw daily rows={len(best)}")
            else:
                errors.append("zhcw daily rows=0")
        except Exception as e:
            errors.append(f"zhcw daily: {type(e).__name__} {e}")

    # D. GitHub 镜像（境外稳定，但通常滞后一天）
    if len(best) < 30:
        try:
            d = json.loads(_get(mirror, timeout=40, xhr=False))
            mir = []
            for x in d.get("draws", []):
                digs = x.get("numbers", {}).get("digits", [])
                if len(digs) == 3:
                    mir.append({"issue": int(x["issue"]), "nums": sorted(digs)})
            mir.sort(key=lambda z: z["issue"])
            if len(mir) >= 20:
                print(f"SOURCE_OK: mirror rows={len(mir)} (may lag)")
                # 合并而非覆盖，保留已有的更新数据
                merged = {x["issue"]: x for x in mir}
                for x in best:
                    merged[x["issue"]] = x
                best = sorted(merged.values(), key=lambda z: z["issue"])
            else:
                errors.append(f"mirror rows={len(mir)}")
        except Exception as e:
            errors.append(f"mirror: {type(e).__name__} {e}")

    # E. 新鲜度补齐：无论上面拿到什么，都用免注册接口核对最新一期
    latest = fetch_latest_api()
    if latest:
        have = {x["issue"] for x in best}
        if latest["issue"] not in have:
            best.append(latest)
            best.sort(key=lambda z: z["issue"])
            print(f"SOURCE_PATCH: apihz 补入 {latest['issue']} {latest['nums']}")
        else:
            print(f"SOURCE_CHECK: apihz 与已有数据一致（{latest['issue']}）")

    if not best:
        print("SOURCE_ERRORS:", " | ".join(errors))
    elif errors:
        print("SOURCE_NOTES:", " | ".join(errors))
    return best

def shape(nums):
    return "豹子" if nums[0] == nums[1] == nums[2] else ("组三" if nums[0] == nums[1] or nums[1] == nums[2] else "组六")

def hit_z6(nums):
    return len(set(nums)) == 3 and set(nums) <= SET

def has_adjacent(nums):
    s = sorted(set(nums))
    return any(b - a == 1 for a, b in zip(s, s[1:]))

def _bj_now():
    """北京时间（UTC+8，无夏令时）。用 timezone-aware 取 UTC 再偏移。"""
    utc = datetime.datetime.now(datetime.timezone.utc)
    return utc.astimezone(datetime.timezone(datetime.timedelta(hours=8))).replace(tzinfo=None)


def _bonus_info(day):
    """派奖期监控：今日是否落在现金加奖窗口；不在则给出下一个窗口的开启日与天数。
    窗口表见 bonus_window.py（与 meeting3/bonus_scan.md 同源，省市公告实证）。
    返回 (in|out, 窗口名, 距今天数, 开启日, 样本说明)。"""
    try:
        from bonus_window import bonus_window, WINDOWS
    except Exception as e:
        print(f"BONUS_SKIP: {type(e).__name__}: {e}")
        return None
    cash = {w[0] for w in WINDOWS if w[3]}
    note = "少量"
    hit, name = bonus_window(day)
    if hit and name in cash:
        return ("in", name, 0, day, note)
    for i in range(1, 400):
        d = day + datetime.timedelta(days=i)
        h, n = bonus_window(d)
        if h and n in cash:
            return ("out", n, i, d, note)
    return None


def build_section(draws, ledger=None):
    last = draws[-1]
    nums = last["nums"]
    nxt = last["issue"] + 1
    # 日期锚定「开奖归属日」，不是脚本运行日（UTC 跨天时会标错期）：
    # 北京 21:15 前跑，最新一期是昨天开的；21:15 后跑，是今天开的。
    bj = _bj_now()
    draw_day = bj.date()
    if bj.hour < 21 or (bj.hour == 21 and bj.minute < 15):
        draw_day -= datetime.timedelta(days=1)
    today = draw_day.strftime("%Y-%m-%d")
    tomorrow = (draw_day + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    stamp = bj.strftime("%Y-%m-%d %H:%M")
    recent10 = draws[-10:]
    h10 = sum(1 for d in recent10 if hit_z6(d["nums"]))
    recent30 = draws[-30:]
    has2_30 = sum(1 for d in recent30 if 2 in d["nums"])
    adj30 = sum(1 for d in recent30 if has_adjacent(d["nums"]))
    wins = 0
    for i in range(2, len(draws)):
        if any(hit_z6(draws[j]["nums"]) for j in range(i - 2, i + 1)):
            wins += 1
    roll3 = wins / (len(draws) - 2) * 100
    today_hit = hit_z6(nums)
    hit_class = "p-ok" if today_hit else "p-bad"
    hit_text = "命中（真兑奖）" if today_hit else "未中"
    streak2 = 0
    for d in reversed(draws):
        if 2 in d["nums"]:
            streak2 += 1
        else:
            break
    rest2 = 0
    for d in reversed(draws):
        if 2 not in d["nums"]:
            rest2 += 1
        else:
            break
    two_state = f"含2连续 {streak2} 期" if streak2 else f"已休息 {rest2} 期"

    # ── 出票清单：14 张 4 码小复式的两种打法（当期号码在哪几张里能兑奖，逐张标出）──
    nset = set(nums)
    t_hits30 = []
    t_today = []
    for tk in TICKETS:
        ts = set(int(c) for c in tk)
        t_hits30.append(sum(1 for x in draws[-30:]
                            if len(set(x["nums"])) == 3 and set(x["nums"]) <= ts))
        t_today.append(len(nset) == 3 and nset <= ts)
    detail = {tk: (h30, hit) for tk, h30, hit in zip(TICKETS, t_hits30, t_today)}

    # 打法A：14 张单打，每张 8 元，限号只损一张
    tk_cells = []
    for tk, h30, hit in zip(TICKETS, t_hits30, t_today):
        tag = " ◀本期中" if hit else ""
        bg = "background:#f0fdf6;border-color:#0a6" if hit else ""
        tk_cells.append(
            f'<span class="mono" style="display:inline-block;margin:0 8px 6px 0;'
            f'padding:2px 6px;border-radius:5px;border:1px solid #dbe3ef;{bg}">'
            f'<b>{tk}</b> <span style="opacity:.6;font-size:11px">近30期{h30}</span>{tag}</span>')
    tickets_html = "".join(tk_cells)
    t_total = sum(t_hits30)

    # 打法B：4 张票（4/4/3/3）每张 24-32 元，全部 ≥20 元门槛，适配山东式派奖
    groups = [TICKETS[0:4], TICKETS[4:8], TICKETS[8:11], TICKETS[11:14]]
    g_rows = []
    for gi, g in enumerate(groups, 1):
        amt = len(g) * 8
        gh = sum(detail[tk][0] for tk in g)
        gh_hit = any(detail[tk][1] for tk in g)
        bg = "background:#f0fdf6" if gh_hit else ""
        tag = " ◀本期中" if gh_hit else ""
        cells = " ".join(
            f'<b style="color:#0a6">{"<u>"+tk+"</u>" if detail[tk][1] else tk}</b>'
            for tk in g)
        g_rows.append(
            f'<tr style="{bg}"><td>第{gi}张</td><td class="mono">{cells}</td>'
            f'<td class="num">近30期{gh}{tag}</td></tr>')
    groups_html = "".join(g_rows)
    all_hit = [tk for tk in TICKETS if detail[tk][1]]

    # ── 派奖期监控：现金加奖窗口内组选6 奖金更高，是唯一能减亏的时段 ──
    bi = _bonus_info(draw_day)
    if bi is None:
        bonus_txt = "窗口表未加载（脚本缺 bonus_window.py）"
        bonus_bg = ""
    elif bi[0] == "in":
        bonus_txt = ("<b style='color:#b45309'>当前落在 %s</b>：该档期历年有省份对组选6 现金加奖"
                     "（各省独立、额度 200–273 元）。<b>开奖前先核对本省福彩公告</b>；"
                     "本省未派奖时按基准 173 元。此档期是全年少亏的时段，其余时间不建议追。" %
                     (bi[1]))
        bonus_bg = "background:#fffbeb"
    else:
        bonus_txt = ("当前为常规期（组选6 单注 173 元）。下一个证实现金加奖窗口：<b>%s</b>，"
                     "约 %d 天后（%s 开启）。该档期历年组选6 上调至 200–273 元，"
                     "仍有缺口但亏得最少。" % (bi[1], bi[2], bi[3].strftime("%Y-%m-%d")))
        bonus_bg = ""

    # ── 全部指标改为从抓到的真实开奖数据现算，不再写死，保证与本地算法同步 ──
    n_all = len(draws)
    z6_all = sum(1 for d in draws if len(set(d["nums"])) == 3)
    hit_all = sum(1 for d in draws if hit_z6(d["nums"]))
    cov_win = hit_all / n_all if n_all else LONG_COV       # 当前窗口实测覆盖率
    pstar = 112.0 / LONG_COV                                # 保本赔付额用全历史校准，避免窗口抖动
    p3 = 1 - (1 - LONG_COV) ** 3                            # 3期内至少中一次（全历史口径）
    # 各数字出现率（用于杀号理由），长期值与窗口值都给出
    rate_w = {d: sum(1 for x in draws if d in x["nums"]) / n_all for d in range(10)}
    kill_txt = "、".join(
        f"{d}（长期 {100*LONG_RATE[d]:.2f}% / 近{n_all}期 {100*rate_w[d]:.2f}%）"
        for d in sorted(set(range(10)) - SET))
    r2 = LONG_RATE[2]
    n_zh3 = sum(1 for d in draws if len(set(d["nums"])) != 3)   # 组三+豹子期数

    notes9 = [" ".join(map(str, sorted(c))) for c in combinations(SET, 3)
              if 2 in c and any(b - a == 1 for a, b in zip(sorted(c), sorted(c)[1:]))]
    color = "#f0fdf6" if today_hit else "#fef2f2"

    # ── 7/6/5 码缩码阶梯（按长期数字频率从 8 码里由冷到热剔除，无前视偏差）──
    # 实测：8码33.99/7码21.75/6码12.28/5码6.18（全期），注数与成本同步下降
    _order = sorted(SET, key=lambda c: LONG_RATE[c])          # 最冷在前
    _ladder = {}
    for _k in (8, 7, 6, 5):
        _keep = sorted(c for c in SET if c not in _order[:8 - _k])
        _ladder[_k] = "".join(map(str, _keep))
    _lrows = []
    # 近 100 期必须用「全量台账」算，不能用抓到的窗口：
    # 生产环境 fetch 常只拿到 30 期镜像，用 draws[-100:] 会变成「近 31 期」标成「近 100 期」。
    _hist = ledger if (ledger and len(ledger) >= 100) else draws
    _w = min(100, len(_hist))
    _seg = _hist[-_w:]

    for _k in (8, 7, 6, 5):
        _c = _ladder[_k]
        _cs = set(int(x) for x in _c)
        _h100 = sum(1 for x in _seg if len(set(x["nums"])) == 3 and set(x["nums"]) <= _cs)
        _r = 33.9943 if _k == 8 else 100.0 * LADDER_LONG[_k]   # 8 码用实测 3000/8825
        _r100 = 100.0 * _h100 / _w
        _kill = "、".join(str(c) for c in sorted(SET - _cs)) or "—"
        _hl = ' style="background:#f0fdf6"' if _k == 8 else ""
        _lrows.append(
            f'<tr{_hl}><td><b>{_k} 码</b>{"（基准）" if _k == 8 else ""}</td>'
            f'<td class="mono"><b>{_c}</b></td>'
            f'<td>杀 {_kill}</td>'
            f'<td class="num">{_r:.2f}%</td><td class="num">{_r100:.1f}%</td>'
            f'<td class="num">{LADDER_ROLL3[_k]:.1f}%</td>'
            f'<td class="num">{LADDER_STREAK[_k]} 期</td>'
            f'<td class="num">{LADDER_GAP[_k]} 期</td></tr>')
    _wlab = f"近{_w}期"
    _r100map = {}
    for _k in (7, 6, 5):
        _cs = set(int(x) for x in _ladder[_k])
        _h = sum(1 for x in _seg if len(set(x["nums"])) == 3 and set(x["nums"]) <= _cs)
        _r100map[_k] = 100.0 * _h / _w
    _r100a, _r100b, _r100c = _r100map[7], _r100map[6], _r100map[5]
    ladder_html = "".join(_lrows)
    win7 = _ladder[7]


    # ── 「今天为什么剔这几个号」：逐数字三窗口状态 + 动态会剔谁 ──
    # 口径：这是观察仪表，不是换号依据。动态剔号已实测（cold100.md）：W=100 剔最冷 20.9971%，
    # 固定剔 2 是 21.7450%，动态更差。所以页面必须写明「不换」。
    def _winrate(c, n):
        seg = _hist[-n:] if len(_hist) >= n else _hist
        return 100.0 * sum(1 for x in seg if c in x["nums"]) / len(seg)
    def _omission(c):
        k = 0
        for x in reversed(_hist):
            if c in x["nums"]:
                break
            k += 1
        return k
    _drows = []
    for _c in range(10):
        # 全期列必须用 8825 期锁定值，不能用 _hist（只有 317 期台账）
        _lr = 100.0 * LONG_RATE[_c]
        _r100 = _winrate(_c, 100)
        _r30 = _winrate(_c, 30)
        _om = _omission(_c)
        _in8 = _c in SET
        if not _in8:
            _tag, _cls = "已杀（8码外）", "p-bad"
        elif abs(_r100 - _lr) <= 3:
            _tag, _cls = "正常", "p-mid"
        elif _r100 > _lr:
            _tag, _cls = "近期偏热", "p-ok"
        else:
            _tag, _cls = "近期偏冷", "p-bad"
        _bg = "" if _in8 else ' style="background:#fef2f2"'
        _drows.append(
            f'<tr{_bg}><td class="num"><b>{_c}</b></td>'
            f'<td class="num">{_lr:.2f}%</td><td class="num">{100.0*RATE300[_c]:.2f}%</td>'
            f'<td class="num">{_r100:.2f}%</td><td class="num">{_r30:.2f}%</td>'
            f'<td class="num">{_om}</td><td><span class="pill {_cls}">{_tag}</span></td></tr>')
    digits_html = "".join(_drows)

    # "今天为什么杀这几个号"：正面理由 + 逐数字状态 + 剔号代价 + 四种换号规律实测
    _c100_all = {c: sum(1 for x in _hist[-100:] if c in x["nums"]) for c in range(10)}
    _c100_in8 = {c: _c100_all[c] for c in SET}
    _dyn_in8 = min(sorted(SET), key=lambda c: _c100_in8[c])
    _dyn_all = min(range(10), key=lambda c: _c100_all[c])
    _fixed_kill = sorted(set(range(10)) - SET)

    # 「今天杀了谁」一句话（每期自动变）
    _hot_lvl = max(LONG_RATE[k] for k in _fixed_kill)
    _cooler = [c for c in sorted(SET) if LONG_RATE[c] > _hot_lvl]
    _kill_line = (
        f'今天（第 {nxt} 期）<b>杀 {"、".join(str(c) for c in _fixed_kill)}</b>。'
        '理由只有一条：这两个数字 8825 期长期出现率是 '
        + "、".join(f'{c} = {100*LONG_RATE[c]:.2f}%' for c in _fixed_kill)
        + '（理论 27.10%），在 10 个数字里最冷；'
        + (f'留下的 {"、".join(str(c) for c in _cooler)} 都比它们热。' if _cooler else '')
    )

    # 剔号代价表
    _cost_rows = []
    for _c in sorted(SET, key=lambda k: -KILL_COST[k][0]):
        _h, _loss = KILL_COST[_c]
        _cost_rows.append(
            f'<tr><td class="num"><b>{_c}</b></td><td class="num">{_h}</td>'
            f'<td class="num">{100.0*_h/8825:.3f}%</td>'
            f'<td class="num">{_loss} 期</td><td class="num">{100.0*_loss/3000:.1f}%</td></tr>')
    cost_html = "".join(_cost_rows)

    # 四种换号规律实测表
    _rule_rows = "".join(
        f'<tr><td>{_n}</td><td class="num">{_r:.4f}%</td>'
        f'<td class="num">{"+" if _r>21.7450 else ""}{_r-21.7450:.2f}pp</td>'
        f'<td style="font-size:11.5px;color:#64748b">{_why}</td></tr>'
        for _n, _r, _why in KILL_RULES)
    rules_html = _rule_rows

    # 今日读数：上期跨度 / 和值 / 大号个数，用于"今天为什么这么买"
    _last = _hist[-1]
    _lsp = max(_last["nums"]) - min(_last["nums"])
    _lsum = sum(_last["nums"])
    _lbig = sum(1 for v in _last["nums"] if v >= 5)
    _lbig_txt = ["0 个大号（全小）", "1 个大号", "2 个大号", "3 个大号（全大）"][_lbig]
    _band_name = ("小跨度带 0–2" if _lsp <= 2 else "中跨度带 3–5" if _lsp <= 5
                  else "宽跨度带 6–7" if _lsp <= 7 else "极大跨度带 8–9")
    _band_rate = {0: 34.68, 1: 34.68, 2: 34.68, 3: 33.56, 4: 33.56, 5: 33.15,
                  6: 35.21, 7: 35.21, 8: 32.75, 9: 32.75}[_lsp]
    _band_n = {0: 1407, 1: 1407, 2: 1407, 3: 2390, 4: 2390, 5: 1285,
               6: 2380, 7: 2380, 8: 1362, 9: 1362}[_lsp]
    _bigrate = [31.52, 33.60, 34.55, 35.90][_lbig]
    _bigdelta = _bigrate - 33.9943
    _today_tone = ("今天读数偏顺：上期%s，历史同状态命中率 %.2f%%，略高于长期均值。" % (_lbig_txt, _bigrate)
                   if _bigdelta > 0.3 else
                   "今天读数偏紧：上期%s，历史同状态命中率 %.2f%%，低于长期均值 33.99%%。" % (_lbig_txt, _bigrate)
                   if _bigdelta < -0.3 else
                   "今天读数中性：上期%s，历史同状态命中率 %.2f%%，与长期均值 33.99%% 基本一致。" % (_lbig_txt, _bigrate))

    # 俞式阶梯资金表（8825 期全样本实测）
    _yu_rows = "".join(
        f'<tr><td>{_n}</td><td class="num">{_r}</td><td class="num">{_c}</td>'
        f'<td class="num">{_f}</td></tr>'
        for _n, _r, _c, _f in YU_LADDER)

    dissect_html = f'''<div class="card"><h3><span class="n">★</span> 今天为什么这样买 · 第 {nxt} 期</h3>

<div class="note" style="margin:0 0 12px;background:#eff6ff;border-left:3px solid #2563eb;font-size:13px">
<b>{_today_tone}</b><br>
上期第 {last["issue"]} 期开 <b>{last["nums"][0]} {last["nums"][1]} {last["nums"][2]}</b>，跨度 <b>{_lsp}</b>（{_band_name}），
和值 <b>{_lsum}</b>，{_lbig_txt}。跨度 {_lsp} 这一档历史后续 8 码命中 <b>{_band_rate:.2f}%</b>（{_band_n} 期）。
</div>

<h4 style="margin:14px 0 6px;font-size:13.5px">一、今天买什么：<b>01245689</b>（8 码 56 注）</h4>
<p style="margin:0 0 10px;font-size:12.5px;color:#475569;line-height:1.85">
它不是"杀掉了 3 和 7"这么一句空话。它等价于<b>一个区间判断</b>：每期开出的三个数字，落在 10 个数字的中间段（0–6，去掉 3），且大号最多一个（7、8、9 里只留 8）。<br>
这个判断在 8825 期里成立 <b>3000 次 = 33.99%</b>，组六期里成立 <b>47.02%</b>。同期 45 个 8 码集合的平均值是 33.74%——1126 只比平均高 0.26 个百分点，属于正常浮动，<b>它不是靠"选得准"，是靠"每次都在场"</b>。
</p>

<h4 style="margin:16px 0 6px;font-size:13.5px">二、按俞的路子会怎么做：<b>7 码起步，没中升 8 码，再没中升 9 码</b></h4>
<p style="margin:0 0 8px;font-size:12.5px;color:#475569">他说的"该杀就杀、适当技术性亏损"就是这套阶梯。8825 期原样跑一遍：</p>
<table><thead><tr><th>档位</th><th class="num">单期命中</th><th class="num">平均几期中一次</th><th class="num">用到多少期</th></tr></thead><tbody>
{_yu_rows}
</tbody></table>
<div class="note" style="margin-top:8px;margin-bottom:0;font-size:12.5px">
阶梯合计命中 <b>35.59%</b>（3141/8825），比固定 8 码的 33.99% 高 1.6 个百分点；<b>3 期内中一次 74.12%</b>，7 期内 <b>98.47%</b>，最长等待 15 期。<br>
代价是平均每期 <b>117.47 元</b>（固定 8 码 112 元），并且 <b>7 码起步那 3142 期只有 21.52% 命中</b>——前 3 期更容易空手。<br>
<b>结论：这套阶梯能把"几期内中一次"做上去，但单期命中率不会超过 8 码本身。</b>要稳就固定 8 码，要"感觉常中"就用阶梯。
</div>

<h4 style="margin:16px 0 6px;font-size:13.5px">三、为什么今天不换号</h4>
<table><thead><tr><th>换法</th><th class="num">7码命中</th><th class="num">vs 固定剔2</th><th>实测结论</th></tr></thead><tbody>
<tr><td>近 100 期剔最冷</td><td class="num">20.9971%</td><td class="num">-0.75pp</td><td>窗口 30/50/200/300 为 21.40/21.23/20.89/20.66%，越长越差</td></tr>
<tr><td>按上期跨度剔</td><td class="num">20.4782%</td><td class="num">-1.27pp</td><td>跨度 5 档挑出 5 个不同数字，不稳定</td></tr>
<tr><td>按遗漏剔（越久越出）</td><td class="num">21.0789%</td><td class="num">-0.67pp</td><td>遗漏 5 档出现率 27.11/26.98/27.30/27.81/27.56%，不单调</td></tr>
<tr><td>按上月组三缺口剔</td><td class="num">21.8244%</td><td class="num">+0.08pp</td><td>五档建议杀 1/7/4/2/5，五个不同答案</td></tr>
</tbody></table>

<h4 style="margin:16px 0 6px;font-size:13.5px">四、跨度这条路走通了：它可以缩形态，但不能提命中</h4>
<p style="margin:0 0 8px;font-size:12.5px;color:#475569;line-height:1.85">
你说的"跨度不会很大"是对的——跨度确实只在 <b>2–9</b> 之间（0、1 跨度需要两数相同以上），跨度 ≤5 占 57.60%。<br>
但<b>跨度序列前后期不相关</b>：lag1 r=+0.0021、lag2 −0.0049、lag3 −0.0138，全部在噪声内。连续 2 期跨度 ≤5 的比例 33.43%，理论独立值 33.17%——差 0.26pp。所以"上期跨度小→下期还小"不成立。<br>
有用的是另一件事：<b>跨度 ∈ {{3,4,5}} 只有 52 个组六形态</b>，能直接排除 68 个形态（56.67%）。但只买这 52 形的命中率是 <b>30.90%</b>（组六期 42.74%），<b>低于 1126 的 33.99%</b>。缩形态省了钱，命中率同比降——和 7/6/5 码是同一件事。
</p>

<div class="note warn" style="margin-top:10px;margin-bottom:0">
<b>五、倍投这件事必须说清楚：</b>7 码 7 期倍投（1/2/4/8/16/32/64），8825 期实测<b>失败 434 次</b>（连亏满 7 期），最长连亏 <b>32 期</b>。7 期窗口挡不住，要扛过 32 期连亏所需本金是 <b>30 万亿元</b>量级。<br>
俞能滚动，是因为他用小注（10 元起）扛前期，并且有前面赢的钱垫；<b>换成 70 元基注的 7 倍倍投，实测总净收益 −309 万</b>，比不倍投的固定 8 码（−46.9 万）差 6.6 倍。<br>
<b>能学的是"升档兜底"，不能学的是"倍投加仓"。</b>
</div>
</div>'''

    # ── 月度「组三+豹子」监控：按真实日历月归属（每年 351 期 = 365 天，扣除休市）──
    _ISSUES_PER_YEAR = 351.0
    def _month_of_issue(seq):
        return min(12, max(1, int((seq - 1) * 12 // _ISSUES_PER_YEAR) + 1))
    def _issue_range(year_month):
        lo = int((year_month - 1) * _ISSUES_PER_YEAR / 12) + 1
        hi = int(year_month * _ISSUES_PER_YEAR / 12)
        return lo, hi

    _yi = int(str(last["issue"])[:4])
    _seq = int(str(last["issue"])[4:])
    _mm = _month_of_issue(_seq)
    _lo, _hi = _issue_range(_mm)
    _mcur = [x for x in draws if int(str(x["issue"])[:4]) == _yi
             and _lo <= int(str(x["issue"])[4:]) <= _seq]
    _mnon6 = sum(1 for x in _mcur if len(set(x["nums"])) < 3)
    _mleft = max(0, _hi - _seq)
    _exp = MONTH_NON6_MEAN
    _need = max(0, round(_exp) - _mnon6)
    _mclass = "p-ok" if _mnon6 >= round(_exp) else "p-bad"
    monthly_html = (
        f'<div class="card"><h3>本月「组三 + 豹子」进度（{_yi} 年 {_mm} 月，第 {_lo}–{_seq} 期）</h3>'
        f'<table><thead><tr><th>项目</th><th class="num">数值</th><th>说明</th></tr></thead><tbody>'
        f'<tr><td>本月已开</td><td class="num">{len(_mcur)} 期</td><td>其中组三 + 豹子 <b>{_mnon6}</b> 次</td></tr>'
        f'<tr><td>历史月均</td><td class="num">{_exp:.2f} 次</td><td>300 个自然月实测，标准差 2.32，最小 2 / 最大 16</td></tr>'
        f'<tr><td>本月进度</td><td class="num"><span class="pill {_mclass}">{_mnon6} / {round(_exp)}</span></td>'
        f'<td>{"已达标" if _mnon6 >= round(_exp) else f"还差 {_need} 次，本月剩余 {_mleft} 期"}</td></tr>'
        f'</tbody></table>'
        f'<div class="note" style="margin-top:10px;margin-bottom:0">'
        f'<b>月底补号（第 4 次会议记录）：</b>"月前没开够、月底会补组三豹子"，'
        f'例 2026-11-27 开出 300。本页按月跟踪。<b>该说法在 8825 期上未通过统计检验</b>'
        f'（月总数对前 26 期次数的回归斜率 0.978，H0:斜率=1 的 t=−1.02；分档后段率 22.6%–29.4% 无单调）。'
        f'仅作观察仪表，不当加注依据。</div></div>'
    )
    return f'''<!--DAILY-START-->
<section id="daily">
<h2><span class="n">★</span>今日推荐 · 1126 算法（自动更新）</h2>
<p class="lede">每天 22:00 / 23:00 自动抓取福彩开奖，结算当日、追加进下方逐期台账，并给出下一期选码。本区块由定时任务维护。</p>
<p class="lede" style="margin-top:-6px"><b>最后更新时间：{stamp}（北京时间）</b>｜本次已结算第 {last["issue"]} 期。若此时间距今超过 30 小时，说明定时任务未跑通。</p>
<div class="card" style="border:2px solid #2563eb">
<h3><span class="n">★</span> 第 {nxt} 期（{tomorrow} 21:15 开奖）· 买这几个码</h3>
<p style="margin:2px 0 10px;font-size:12.5px;color:#475569">由 1126 的固定 8 码 <b class="mono">01245689</b> 逐级剔码得到，剔除顺序按各数字 8825 期长期出现率由冷到热（无前视偏差）。</p>
<table><thead><tr><th>档位</th><th>买码</th><th>剔除</th><th class="num">全期命中</th><th class="num">{_wlab}</th><th class="num">3期内中一次</th><th class="num">最长连亏</th><th class="num">平均几期中一次</th></tr></thead><tbody>
{ladder_html}
</tbody></table>
<div class="note warn" style="margin-top:10px;margin-bottom:0">
<b>「剔除」这一列不是预测，是省钱档。论证九条（8825 期实测）：</b>
<ol style="margin:6px 0 0 18px;padding:0;line-height:1.85">
<li><b>杀冷号没有预测力。</b>每期取前 30 期出现最少的数字当「冷号」，它下一期仍出现 <b>27.11%</b>，与纯随机 27.10% 无差别（窗口 50/100 分别 27.37%/27.31%，同样无差别）。所以任何「杀冷号」都不成立。</li>
<li><b>剔号的真实代价是误杀。</b>8 码全期中奖 3000 期；剔掉 2 会误杀 <b>1081 期（占全部中奖期的 36.0%）</b>，剩 1919 期 = 21.75%。这就是 7 码只有 8 码六成半的原因，不是「更准」。</li>
<li><b>「杀 3 杀 7 留 2」在频率上站不住。</b>实测 2 是<b>最冷</b>的数字（26.05%），3 是 27.08%、7 是 27.23%，都比 2 热。留最冷的、杀更热的，与「杀冷号」逻辑正好相反。1126 的 47.02% 落在 45 个 8 码集合的第 13 位（72.4 百分位），属正常浮动。</li>
<li><b>动态剔最冷号反而更差。</b>每期现算最冷号再剔，7 码命中 <b>21.27%</b>，低于固定剔 2 的 21.75%。说明「按冷号换着剔」是负收益。</li>
<li><b>换哪个被剔都一样。</b>8 码剔 1 个共 9 种，最好剔 2（1919 期）、最差剔 9（1837 期），极差 0.93 个百分点，全在噪声内。所以「剔哪个」不是选择，是取舍：剔得越少命中越高，7 码必然比 8 码低 12.2 个点。</li>
<li><b>误杀的代价可以量化。</b>8 码平均 2.9 期中一次，7 码 4.6 期、6 码 8.1 期、5 码 16.2 期。5 码省下的钱，换来的是「平均等 16 期才中一次」，最长要等 85 期。</li>
<li><b>「杀上期号」也没用。</b>上期是组六时，把上期 3 个数剔掉、买剩 7 码，命中 <b>21.32%</b>；同条件下固定 8 码是 34.26%，<b>低 12.95 个百分点</b>。而「下期完全避开上期三数」实测 29.46%，理论 29.17%（z=+0.43，p=0.66）——上一期开什么，对下一期没有任何约束。</li>
<li><b>百十个位没有位置偏差。</b>三位置各自 0-9 的卡方 p = 0.53 / 0.08 / 0.48；三位两两独立性的 p = 0.54 / 0.10 / 0.29。全部不显著，不存在「某位偏爱某数」。</li>
</ol>
<b>另两条排除（本轮新验）：</b>① 和值×跨度联合筛，严格 walk-forward 选 56 个形态命中 46.34%，<b>低于</b>固定 8 码同区间的 47.08%；② 1126 逐年排名在 1~45 之间摆动，2014-2026 与 2002-2013 两段差 z=−1.08（p=0.28），不存在「被时代淘汰」。
<b>结论：</b>7/6/5 码的价值只有一条——同样的钱买更少的注数，代价是命中率同比降。它们不是「更准的杀号」，不能拿它当缩水依据。
</div>
<div class="note" style="margin-top:10px;margin-bottom:0;background:#eff6ff;border-left:3px solid #2563eb">
<b>今日首选 7 码 <span class="mono">{win7}</span></b>，全期命中 {100*LADDER_LONG[7]:.2f}%、
{_wlab} {_r100a:.1f}%、3 期内中一次 {LADDER_ROLL3[7]:.1f}%；
要贴着 1126 全量打就仍用 8 码 <b class="mono">01245689</b>（3 期内中一次 70.9%，最长连亏 25 期）。
6 码 <b class="mono">{_ladder[6]}</b>（{100*LADDER_LONG[6]:.2f}% / {_wlab} {_r100b:.1f}%）、
5 码 <b class="mono">{_ladder[5]}</b>（{100*LADDER_LONG[5]:.2f}% / {_wlab} {_r100c:.1f}%）为省成本档，按预算自行取舍。
</div>
</div>
{dissect_html}
<div class="card">
<h3>{today} · 第 {last["issue"]} 期开奖结果</h3>
<table><thead><tr><th>期号</th><th class="num">开奖号</th><th class="num">形态</th><th class="num">01245689</th><th class="num">结果</th></tr></thead><tbody>
<tr style="background:{color}"><td>{last["issue"]}</td><td class="num mono">{" ".join(map(str, nums))}</td><td class="num">{shape(nums)}</td><td class="num">{"全在码里" if set(nums) <= SET else "有号不在码里"}</td><td><span class="pill {hit_class}">{hit_text}</span></td></tr>
</tbody></table>
</div>
{monthly_html}
<div class="card">
<h3>出票与监控（第 {nxt} 期）</h3>
<table><thead><tr><th>项目</th><th>内容</th></tr></thead><tbody>
<tr style="background:#f0fdf6"><td><b>出票拆单</b></td><td><b>不要打 1 张 8 码复式，按下面 14 个 4 码组去打</b>（零重复零遗漏；整张 8 码复式只要其中 1 注被限号，官方规则整张不出票）。<div style="margin-top:8px"><b>打法A · 限号风险最小</b></div><div class="mono" style="margin-top:4px">{tickets_html}</div></td></tr>
<tr><td></td><td><b>打法B · 过单票门槛</b>：山东 2024 公告要求组选派奖单票≥20 元才参与，打法A 的小票不达标；用这 4 张。<table style="margin-top:6px"><thead><tr><th>票</th><th>4 码组合</th><th class="num">近30期</th></tr></thead><tbody>{groups_html}</tbody></table><div style="margin-top:5px;opacity:.75;font-size:11.5px">两种打法覆盖完全相同的组合；A 更抗限号，B 更抗门槛，按本省公告选一种，<b>不要两种同时打</b>。</div></td></tr>
<tr style="{bonus_bg}"><td><b>派奖监控</b></td><td>{bonus_txt}</td></tr>
<tr><td><b>数字 2 状态</b></td><td>{two_state}（长期含2率 {100*r2:.2f}% vs 理论 27.10%，8码已含2）</td></tr>
<tr><td><b>近 10 期</b></td><td>8 码真兑奖命中 {h10}/10；近 30 期含2 {has2_30}/30、连号 {adj30}/30</td></tr>
<tr><td><b>本期可跳过？</b></td><td>不能。近 {n_all} 期组三/豹子 {n_zh3} 期（长期 27.71%），这些期必挂；但形态不可预判（卡方 0.113，前后半最优窗口不一致），跳期等于随机少买，实测无收益</td></tr>
<tr><td><b>倍投上限</b></td><td>单期赔付 302 元；盘口单期赔付上限若为 3000 元，约 9 倍撞线 → <b>不要倍投超过 10 倍</b>，否则触发庄家限额拒单</td></tr>
<tr style="background:#fffbf0"><td><b>保本赔付额</b></td><td><b>{pstar:.0f} 元</b>（= 成本 112 元 ÷ 长期覆盖率 {100*LONG_COV:.2f}%，基于 8825 期全历史 2002-2026）。低于此数每期亏，高于此数每期赚。（当前窗口 {n_all} 期实测 {100*cov_win:.2f}%，仅供对照）</td></tr>
</tbody></table>
<div class="note warn" style="margin-top:14px;margin-bottom:0">
<b>口径提醒：</b>真兑奖 = 三码全中且三数不同（组六口径），且只在组六期能兑奖。8 码在组六期命中率 47.02%，全期口径 33.99%（= 72.29% × 47.02%）。
连续未中属正常波动。本页不构成任何投注建议。
</div>
</div>
</section>
<!--DAILY-END-->'''

def main():
    draws = fetch_draws(100)
    if len(draws) < 10:
        raise RuntimeError(f"官网返回期数不足：{len(draws)}")
    # 先追加台账（新期号才会写），再刷新每日预测区块
    try:
        from append_ledger import append_to_html
        n_added, n_total = append_to_html(HTML_PATH, draws)
        print(f"LEDGER: 新增 {n_added} 期，台账共 {n_total} 期")
    except Exception as e:
        print(f"LEDGER_SKIP: {type(e).__name__}: {e}")
    # 读全量台账（供近 100 期统计用，生产环境抓取窗口常只有 30 期）
    ledger = None
    try:
        import os
        dj = os.path.join(os.path.dirname(os.path.abspath(HTML_PATH)), "data.js")
        if os.path.exists(dj):
            with io.open(dj, encoding="utf-8") as f:
                mj = re.search(r"window\.__P=(\[.*?\]);", f.read(), re.S)
            if mj:
                ledger = [{"issue": p["i"], "nums": [int(c) for c in p["draw"]]}
                          for p in json.loads(mj.group(1))]
                print(f"LEDGER_READ: data.js {len(ledger)} 期")
        else:
            with io.open(HTML_PATH, encoding="utf-8") as f:
                mj = re.search(r"const P=(\[.*?\]);", f.read(), re.S)
            if mj:
                ledger = [{"issue": p["i"], "nums": [int(c) for c in p["draw"]]}
                          for p in json.loads(mj.group(1))]
                print(f"LEDGER_READ: index.html {len(ledger)} 期")
    except Exception as e:
        print(f"LEDGER_READ_SKIP: {type(e).__name__}: {e}")
    section = build_section(draws, ledger)
    with io.open(HTML_PATH, "r", encoding="utf-8") as f:
        doc = f.read()
    # 清掉旧的预测区块（含包在外面的 wrap 容器）与旧的导航项
    doc = re.sub(r'<div class="wrap" style="padding-top:16px">\s*<!--DAILY-START-->.*?<!--DAILY-END-->\s*</div>\n?',
                 "", doc, flags=re.S)
    doc = re.sub(r"<!--DAILY-START-->.*?<!--DAILY-END-->\n?", "", doc, flags=re.S)
    doc = re.sub(r'<a href="#daily"[^>]*>.*?</a>\n?', "", doc, flags=re.S)
    # 1) 注入到 <body> 最顶部（hero 之前），打开链接第一屏就是每日预测
    anchor = '<header class="hero"'
    idx = doc.find(anchor)
    if idx >= 0:
        doc = (doc[:idx]
               + '<div class="wrap" style="padding-top:16px">\n' + section + "\n</div>\n"
               + doc[idx:])
    else:
        # 回退：放进正文最前面
        anchor = '<section id="verdict"'
        idx = doc.find(anchor)
        if idx < 0:
            raise RuntimeError("index.html 中找不到注入点")
        doc = doc[:idx] + section + "\n" + doc[idx:]
    # 2) 导航栏加一个显眼入口
    navidx = doc.find('<a href="#verdict">一句话结论</a>')
    if navidx >= 0:
        doc = (doc[:navidx]
               + '<a href="#daily" class="hot">★★ 今日预测</a>\n'
               + doc[navidx:])
    with io.open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(doc)
    last = draws[-1]
    print(f"OK 期号{last['issue']} 号码{last['nums']} {shape(last['nums'])} 命中={hit_z6(last['nums'])}")

if __name__ == "__main__":
    main()
