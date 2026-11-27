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

def build_section(draws):
    last = draws[-1]
    nums = last["nums"]
    nxt = last["issue"] + 1
    today = datetime.date.today().strftime("%Y-%m-%d")
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
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
    notes9 = [" ".join(map(str, sorted(c))) for c in combinations(SET, 3)
              if 2 in c and any(b - a == 1 for a, b in zip(sorted(c), sorted(c)[1:]))]
    color = "#f0fdf6" if today_hit else "#fef2f2"
    return f'''<!--DAILY-START-->
<section id="daily">
<h2><span class="n">★</span>每日预测 · 1126 算法（自动更新）</h2>
<p class="lede">每天 22:00 自动抓取福彩开奖，结算当日、追加进下方逐期台账，并给出次日预测。本区块由定时任务维护。</p>
<div class="card">
<h3>{today} · 第 {last["issue"]} 期开奖结果</h3>
<table><thead><tr><th>期号</th><th class="num">开奖号</th><th class="num">形态</th><th class="num">01245689</th><th class="num">结果</th></tr></thead><tbody>
<tr style="background:{color}"><td>{last["issue"]}</td><td class="num mono">{" ".join(map(str, nums))}</td><td class="num">{shape(nums)}</td><td class="num">{"全在码里" if set(nums) <= SET else "有号不在码里"}</td><td><span class="pill {hit_class}">{hit_text}</span></td></tr>
</tbody></table>
</div>
<div class="card">
<h3>次日预测 · 第 {nxt} 期（{tomorrow} 21:15 开奖）</h3>
<table><thead><tr><th>项目</th><th>内容</th></tr></thead><tbody>
<tr style="background:#f0fdf6"><td><b>选码</b></td><td class="mono"><b>01245689</b>（固定 8 码，56 注 / 112 元，每期不变）</td></tr>
<tr><td><b>杀号</b></td><td>杀 {KILL}（3 最冷 22.33%、7 次冷 23.00%）</td></tr>
<tr><td><b>数字 2 状态</b></td><td>{two_state}（长期含2率 38.14% vs 随机 27.10%，8码已含2）</td></tr>
<tr><td><b>加强过滤（9注）</b></td><td class="mono">{"、".join(notes9)}</td></tr>
<tr><td><b>近 10 期</b></td><td>8 码真兑奖命中 {h10}/10；近 30 期含2 {has2_30}/30、连号 {adj30}/30</td></tr>
<tr><td><b>3 期内中一次</b></td><td>{roll3:.2f}%（滚动窗口，理论 70.72%）</td></tr>
</tbody></table>
<div class="note warn" style="margin-top:14px;margin-bottom:0">
<b>口径提醒：</b>真兑奖 = 三码全中且三数不同（组六口径）。8 码理论命中率 33.60%，前向期望约 38%。
连续未中属正常波动，3 期滚动约 78% 至少中一次。本页不构成任何投注建议。
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
    section = build_section(draws)
    with io.open(HTML_PATH, "r", encoding="utf-8") as f:
        doc = f.read()
    # 清掉旧的预测区块与旧的导航项
    doc = re.sub(r"<!--DAILY-START-->.*?<!--DAILY-END-->\n?", "", doc, flags=re.S)
    doc = re.sub(r'<a href="#daily"[^>]*>.*?</a>\n?', "", doc, flags=re.S)
    # 1) 注入到首屏正文最前面（紧跟导航后的 .wrap 内），保证打开就能看到
    anchor = '<section id="verdict"'
    idx = doc.find(anchor)
    if idx < 0:
        raise RuntimeError("index.html 中找不到 <section id=\"verdict\" 注入点")
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
