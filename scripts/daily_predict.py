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
SET = set(SEL)
KILL = "3、7"
CTX = ssl.create_default_context()

def fetch_draws(count=100):
    # 主源：中彩网 jsonp 接口（GitHub Actions 境外 IP 可访问）
    # 备源：福彩官网（境内可访问，境外 403 时自动跳过）
    sources = []
    today = datetime.date.today()
    start = (today - datetime.timedelta(days=180)).isoformat()
    zhcw = ("https://jc.zhcw.com/port/client_json.php?callback=jQuery1"
            f"&transactionType=10001001&lotteryId=2&issueCount={count}"
            f"&startDate={start}&endDate={today.isoformat()}"
            "&type=2&pageNum=1&pageSize=200")
    cwl = (f"http://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice"
           f"?name=3d&issueCount={count}")
    sources = [
        (zhcw, "https://www.zhcw.com/kjxx/3d/"),
        (cwl, "http://www.cwl.gov.cn/kjxx/3d/kjgg/"),
    ]
    # 备源2：GitHub 镜像仓库（境外稳定，但每天早上才更新当晚数据）
    mirror = "https://raw.githubusercontent.com/wenjinliuu/lottery-data-repo/main/public_data/v2/draws/fc3d.json"
    errors = []
    for url, referer in sources:
        # 重试两次：中彩网对境外偶发断连
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
                    "Referer": referer,
                    "Accept": "*/*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "X-Requested-With": "XMLHttpRequest",
                    "Connection": "close",
                })
                with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
                    text = r.read().decode("utf-8")
                if text.startswith("jQuery"):
                    text = text[text.find("(") + 1:text.rfind(")")]
                data = json.loads(text)
                out = []
                if "data" in data and isinstance(data["data"], list):
                    # 中彩网结构
                    for x in data["data"]:
                        nums = [int(c) for c in str(x.get("frontWinningNum", "")).split()
                                if c.isdigit()]
                        if len(nums) == 3:
                            out.append({"issue": int(x["issue"]), "nums": sorted(nums)})
                else:
                    # 福彩官网结构
                    rows = data.get("result") or data
                    for x in rows:
                        issue = int(str(x["code"]))
                        reds = str(x.get("red", ""))
                        nums = [int(c) for c in reds.split(",") if c.strip().isdigit()]
                        if len(nums) == 3:
                            out.append({"issue": issue, "nums": sorted(nums)})
                out.sort(key=lambda d: d["issue"])
                if len(out) >= 30:
                    print(f"SOURCE_OK: {url.split('/')[2]} rows={len(out)}")
                    return out
                errors.append(f"{url.split('/')[2]}: rows={len(out)}")
                break
            except Exception as e:
                errors.append(f"{url.split('/')[2]}#{attempt}: {type(e).__name__} {e}")
                if attempt == 0:
                    import time
                    time.sleep(3)
    # GitHub 镜像兜底
    try:
        req = urllib.request.Request(mirror, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        })
        with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
            data = json.loads(r.read().decode("utf-8"))
        out = []
        for x in data.get("draws", []):
            digs = x.get("numbers", {}).get("digits", [])
            if len(digs) == 3:
                out.append({"issue": int(x["issue"]), "nums": sorted(digs)})
        out.sort(key=lambda d: d["issue"])
        if len(out) >= 30:
            print(f"SOURCE_OK: raw.githubusercontent.com rows={len(out)} (mirror, may lag)")
            return out
        errors.append(f"mirror: rows={len(out)}")
    except Exception as e:
        errors.append(f"mirror: {type(e).__name__} {e}")
    print("SOURCE_ERRORS:", " | ".join(errors))
    return []

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
<p class="lede">每天 21:45 自动抓取福彩官网开奖，结算当日并给出次日预测。本区块由定时任务维护，历史论证见上方各节。</p>
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
