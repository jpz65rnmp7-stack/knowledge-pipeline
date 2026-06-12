# 知识付费内容聚合 Pipeline

> 景一的自动化知识引擎 — 多平台抓取 → 分类 → 去重 → 质量过滤 → 去AI味改写 → Obsidian 输出

## 架构

```
多平台抓取 (B站/知乎/公众号)
    │   3 平台并发，asyncio + httpx
    ▼
LLM 分类 (8大类 50+子类)
    │   并发 LLM 调用，Semaphore 限流
    ▼
去重 (URL + 标题相似度)
    │   SQLite 持久化
    ▼
质量过滤 (启发式 + LLM 5维评分)
    │   两阶段：快速规则 + 深度评估
    ▼
去AI味改写 (景一风格)
    │   心理学概念 + 国学引用 + 金句生成
    ▼
Obsidian 输出 (Frontmatter + 分类目录)
    │   Claude-商业蒸馏/ 分目录
    ▼
图片生成 (即梦提示词 → dreamina CLI)
```

## 快速开始

### 1. 环境配置

```bash
# 克隆仓库
git clone <repo-url>
cd knowledge-pipeline

# 安装依赖
pip install -r requirements.txt
# 或使用 uv
uv pip install -e .

# 设置 API 密钥
cp .env.example .env
# 编辑 .env，填入你的 DeepSeek API 密钥
export ANTHROPIC_AUTH_TOKEN=sk-your-key-here
```

### 2. 配置平台和关键词

编辑 `config/platforms.yaml`：
- 启用/禁用平台
- 设置每个平台的关键词
- 调整每日抓取上限

编辑 `config/categories.yaml`：
- 调整分类体系
- 修改质量阈值

### 3. 运行

```bash
# 完整运行
python main.py

# 预览模式（不写入 Obsidian）
python main.py --dry-run

# 只跑指定平台
python main.py --platform bilibili

# 调试模式（禁用并发）
python main.py --no-parallel --dry-run

# 自定义 Obsidian vault 路径
python main.py --vault /path/to/your/vault
```

### 4. 生成配图

```bash
# 批量生图（读取已产出文章的即梦提示词）
python -m src.generate_images

# 预览模式
python -m src.generate_images --dry-run

# 指定比例和数量
python -m src.generate_images --ratio 9:16 --limit 3
```

## 目录结构

```
knowledge-pipeline/
├── main.py                  # 顶层入口
├── pyproject.toml           # 项目元数据 + CLI 入口点
├── .env.example             # 环境变量模板
├── config/
│   ├── platforms.yaml       # 平台配置（关键词、开关、限流）
│   ├── categories.yaml      # 分类体系（8大类 + 质量阈值）
│   └── prompts/
│       ├── classify.txt     # 分类器 LLM prompt
│       ├── quality.txt      # 质量评估 LLM prompt
│       └── humanize.txt     # 景一风格改写 LLM prompt
├── src/
│   ├── main.py              # Pipeline 编排器 (async)
│   ├── utils.py             # LLM客户端 + 配置加载 + 工具函数
│   ├── classifier.py        # 内容分类器
│   ├── dedup.py             # 去重追踪器 (SQLite)
│   ├── quality.py           # 质量过滤 (两阶段)
│   ├── humanizer.py         # 去AI味改写器
│   ├── writer.py            # Obsidian 输出写入器
│   ├── generate_images.py   # 即梦生图脚本
│   └── scrapers/
│       ├── base.py          # 抽象基类 + ScrapedArticle 数据类
│       ├── utils.py         # 共享工具 (UA轮换, 限流, HTML清洗)
│       ├── bilibili.py      # B站搜索 API 抓取器
│       ├── zhihu.py         # 知乎搜索页解析器
│       └── wechat_sogou.py  # 搜狗微信搜索抓取器
├── data/                    # 运行时数据 (gitignored)
│   ├── seen_urls.db         # 去重数据库
│   └── generated_images.json # 生图追踪
├── logs/                    # 运行日志 (gitignored)
│   └── pipeline_YYYY-MM-DD.log
└── tests/                   # 测试 (待补充)
```

## 设计决策

### 为什么用异步而不是多线程？
- 爬虫和 LLM 调用都是 I/O 密集型，`asyncio` 比多线程开销更小
- 3 个平台并发抓取 + 关键字间 Semaphore 限流，避免触发反爬
- LLM 分类和质量评估天然独立，并发后吞吐量提升 3-4x

### 为什么分类和质量用 LLM 而不是机器学习模型？
- 中文内容理解需要语义深度，关键词匹配准确率太低
- LLM 可以直接输出结构化 JSON，无需额外解析层
- 成本可控：每篇 800 字摘要 + 1024 token 输出，约 0.001 元/次

### 为什么 Humanizer 保持串行？
- 改写质量是核心价值，需要完整上下文
- 每篇输出 600-1000 字，LLM 生成时间远超过网络延迟
- 串行输出更便于实时观察质量、及时调整

## 扩展指南

### 添加新平台

1. 在 `src/scrapers/` 下创建新文件，继承 `BaseScraper`
2. 实现 `platform_name` 和 `search_keywords()` 方法
3. 在 `src/main.py` 的 `scrapers_map` 中注册
4. 在 `config/platforms.yaml` 中添加配置

### 添加新的 LLM Provider

编辑 `src/utils.py` 的 `LLMClient`：
```python
self.base_url = "https://api.openai.com/v1"
self.model = "gpt-4o"
# 对应调整 call() 中的请求格式
```

## License

MIT
