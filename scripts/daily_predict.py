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

# 7/6/5 码缩码阶梯的长期校准（8825 期实测，2026-11-28 锁定）
# 剔码顺序 = 8 码内长期出现率由冷到热：2(26.05) → 5(26.78) → 6(27.10)
LADDER_LONG = {7: 0.217450, 6: 0.122833, 5: 0.061756}
# 各档「3 期内至少中一次」与「最长连亏」（8825 期全历史实测，2026-11-28 锁定）
# 注意：不能用 data.js 的台账（仅 317 期）现算，否则会把 317 期的结果标成全历史。
LADDER_ROLL3 = {8: 70.9396, 7: 51.4224, 6: 32.4153, 5: 17.4770}
LADDER_STREAK = {8: 25, 7: 32, 6: 61, 5: 85}
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
            f'<td class="num">{LADDER_STREAK[_k]} 期</td></tr>')
    _wlab = f"近{_w}期"
    _r100map = {}
    for _k in (7, 6, 5):
        _cs = set(int(x) for x in _ladder[_k])
        _h = sum(1 for x in _seg if len(set(x["nums"])) == 3 and set(x["nums"]) <= _cs)
        _r100map[_k] = 100.0 * _h / _w
    _r100a, _r100b, _r100c = _r100map[7], _r100map[6], _r100map[5]
    ladder_html = "".join(_lrows)
    win7 = _ladder[7]

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
<table><thead><tr><th>档位</th><th>买码</th><th>剔除</th><th class="num">全期命中</th><th class="num">{_wlab}</th><th class="num">3期内中一次</th><th class="num">最长连亏</th></tr></thead><tbody>
{ladder_html}
</tbody></table>
<div class="note" style="margin-top:10px;margin-bottom:0;background:#eff6ff;border-left:3px solid #2563eb">
<b>今日首选 7 码 <span class="mono">{win7}</span></b>，全期命中 {100*LADDER_LONG[7]:.2f}%、
{_wlab} {_r100a:.1f}%、3 期内中一次 {LADDER_ROLL3[7]:.1f}%；
要贴着 1126 全量打就仍用 8 码 <b class="mono">01245689</b>（3 期内中一次 70.9%，最长连亏 25 期）。
6 码 <b class="mono">{_ladder[6]}</b>（{100*LADDER_LONG[6]:.2f}% / {_wlab} {_r100b:.1f}%）、
5 码 <b class="mono">{_ladder[5]}</b>（{100*LADDER_LONG[5]:.2f}% / {_wlab} {_r100c:.1f}%）为省成本档，按预算自行取舍。
</div>
</div>
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
