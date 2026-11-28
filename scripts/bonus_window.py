"""福彩3D 组选6 派奖窗口判定函数（纯标准库）。

窗口来源：bonus_scan.md §2/§8.1、§1.1（广州跨年档）；硬编码为月/日区间。
跨年窗口（start > end）用「>= start 或 <= end」判定，天然跨越 12→1 月。
现金窗口优先于赠票窗口。
"""

import datetime

# (窗口名, 起月日, 止月日, 是否现金)
# 起止均含端点。start > end 表示跨年区间。
WINDOWS = (
    ("春节/元宵档", (1, 21), (3, 10), True),      # §2「1 月下旬–3 月上旬」
    ("初夏/暑期档", (6, 1), (8, 5), True),        # §8.1「6 月 1 日 – 8 月 5 日」
    ("岁末档", (11, 15), (12, 31), True),         # §2「11 月中–12 月底」
    ("跨年赠票档", (11, 16), (2, 1), False),      # §1.1 广州 2026-11-16 → 2027-02-01
)


def bonus_window(d):
    """d 为 datetime.date；落在已知派奖窗口内返回 (True, 窗口名)，否则 (False, "")。"""
    md = (d.month, d.day)
    # 现金窗口优先
    for is_cash in (True, False):
        for name, start, end, cash in WINDOWS:
            if cash != is_cash:
                continue
            if start <= end:
                hit = start <= md <= end
            else:  # 跨年，如 11-16 → 次年 2-1
                hit = md >= start or md <= end
            if hit:
                return (True, name)
    return (False, "")


if __name__ == "__main__":
    for month in range(1, 13):
        for day in (1, 15):
            d = datetime.date(2026, month, day)
            hit, name = bonus_window(d)
            print("%04d-%02d-%02d -> %s %s" % (
                d.year, d.month, d.day,
                ("IN " + name) if hit else "OUT",
                "" if hit else "-",
            ))
