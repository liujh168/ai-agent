"""
支持 python -m ai_agent 启动。
通过把项目根目录加入 sys.path，让顶层 main.py 可被导入。
"""

import os
import sys

# 确保项目根在 sys.path 中（main.py 所在目录）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from main import main  # noqa: E402

if __name__ == "__main__":
    main()
