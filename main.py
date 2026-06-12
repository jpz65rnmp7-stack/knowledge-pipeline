"""知识付费内容聚合 Pipeline — 顶层入口。

实际逻辑在 src/main.py，这里只是一个便捷重定向。
用法:
  python main.py                    # 完整 pipeline
  python main.py --dry-run          # 预览模式
  python main.py --platform bilibili # 只跑指定平台
"""

import sys
from pathlib import Path

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).parent))

from src.main import main

if __name__ == "__main__":
    sys.exit(main())
