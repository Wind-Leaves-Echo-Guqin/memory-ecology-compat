# models/ — 本地 embedding 模型（离线语义层）

本目录存放 L3 embedding 后端所需的 ONNX 模型，**权重不入库**（见 `.gitignore`：`models/*/`；
本 README 保留）。按需用脚本拉取，走 HF 镜像（`HF_ENDPOINT=https://hf-mirror.com`）：

```bash
export HF_ENDPOINT=https://hf-mirror.com
python src/memory_ecology/fetch_embed_models.py --list              # 查看可选模型
python src/memory_ecology/fetch_embed_models.py bge-small-zh-v1.5   # 拉取选定模型
```

（脚本随 `src/memory_ecology/` 同步到 dev/hermes 树的 `scripts/`，两处同名可跑。）

每个模型目录需三件套：`model.onnx` + `tokenizer.json` + `config.json`。
`lib/similarity.py::OnnxEmbedder` 契约：目录名即 `MEMORY_ECOLOGY_EMBED_MODEL` 的值；
模型根默认 `<生态根>/models`（`MEMORY_ECOLOGY_MODELS` 可覆盖）。

## 选型结论（2026-10-04，golden 182 对实测）

**选定 `bge-small-zh-v1.5` + CLS pooling。** 依据（见 `compare_embed_models.py`
/ `tradeoff_table.py` / `calibrate_thresholds.py`）：

| 后端 | AUC(same\|sim+dis) | AUC(hard\|sim+dis) | 误合并≤10% 时 same 召回 | 182 对耗时 | 体积 |
|---|---|---|---|---|---|
| difflib（旧默认） | 0.901 | 0.798 | 54% | ~0.1s | — |
| ngram | 0.901 | 0.798 | 64% | ~0.1s | — |
| **bge-small-zh-v1.5[cls]** | **0.931** | **0.858** | **74%** | **5s** | **95MB** |
| bge-base-zh-v1.5[cls] | 0.914 | 0.831 | 59% | 27s | 407MB |
| bge-large-zh-v1.5[cls] | 0.926 | 0.841 | 71% | 102s | 1.3GB |

- **CLS > mean**：三个 bge 模型上 CLS 的 AUC 与 hard 召回一致更高（BGE 官方推荐 CLS，
  实测印证）。故 `MEMORY_ECOLOGY_EMBED_POOLING=cls`。
- **small 够用**：bge-small 在 AUC 与 hard 召回上均不逊于 base/large，且快 5~20×、小 4~14×。
  8GB 显存/离线约束下无理由上大模型。
- **text2vec-base-chinese-paraphrase 淘汰**：分数压缩到 0.98+（distinct 中位 0.98），
  完全丧失判别力，FPR=0 下只回收 5/99。

## 部署

生产解释器 `hermes-agent/venv`（Python 3.11）已含 `onnxruntime`/`numpy`/`tokenizers`。
启用（默认仍是 difflib，行为锁定）：

```
MEMORY_ECOLOGY_SIM_BACKEND=embedding
MEMORY_ECOLOGY_EMBED_MODEL=bge-small-zh-v1.5
MEMORY_ECOLOGY_EMBED_POOLING=cls
MEMORY_ECOLOGY_MODELS=<生态根>/models
```

门阈值随后端自动切换（`lib/similarity.py::gate_threshold`），无需手改门脚本。
