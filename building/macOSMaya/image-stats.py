# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Print per-image statistics for viewport captures. Run with mayapy (PySide6).

Reports size, number of distinct colours, the most common colour and the
fraction of pixels that differ from it: a flat background-only capture has one
dominant colour; a rendered cube has many.
"""
import sys
from collections import Counter
from PySide6.QtGui import QImage

for path in sys.argv[1:]:
    img = QImage(path)
    if img.isNull():
        print(f"{path}: unreadable")
        continue
    img = img.convertToFormat(QImage.Format_RGB32)
    w, h = img.width(), img.height()
    step = max(1, (w * h) // 200000)
    counts = Counter(img.pixel(i % w, i // w) & 0xFFFFFF for i in range(0, w * h, step))
    total = sum(counts.values())
    top, n = counts.most_common(1)[0]
    print(f"{path.split('/')[-1]:18s} {w}x{h}  colours={len(counts):6d}  "
          f"dominant=#{top:06x} {100 * n / total:5.1f}%  other={100 * (total - n) / total:5.1f}%")
