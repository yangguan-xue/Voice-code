# TTS 参数说明

## VoxCPM2 (`tts_server.py`)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `text` | (必填) | 要合成的文本 |
| `control` | `""` | 语气控制指令。当前已废弃（模型会把它当内容念出来），传了也被忽略 |
| `cfg_value` | `2.0` | Classifier-Free Guidance 强度。越高模型越遵循 prompt 和 reference audio，语气更稳定；越低自由度越高，但可能走调。推荐 2.0~4.0 |
| `inference_timesteps` | `10` | Diffusion 推理步数。越多质量越高但越慢。10=平衡，15=高质量，20=极高质量 |
| `seed` | `None` | 随机种子。固定则同文本=同输出，不传每次不同 |

## Fish-Speech s2-pro (`fish_gpu.py`)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `text` | (必填) | 要合成的文本 |
| `seed` | `None` | 随机种子。固定则同文本=同输出 |
| `temperature` | `0.8` | 采样温度。0.1~1.0。越低输出越确定、稳定；越高越随机、表现力强。推荐稳定输出用 0.3 |
| `top_p` | `0.8` | Nucleus sampling 阈值。越低采样范围越窄，配合低温度更稳定。推荐 0.5~0.8 |
| `repetition_penalty` | `1.1` | 重复惩罚。>1.0 抑制重复，<1.0 鼓励重复。推荐稳定时用 1.0 关闭 |
| `max_new_tokens` | `512` | 最大生成 token 数。控制输出音频最大长度 |

## 关键区别

| 方面 | VoxCPM2 | Fish-Speech |
|------|---------|-------------|
| 模型类型 | 扩散自回归 (2B) | Qwen3 自回归 (9B) |
| 中英混合 | 依赖参考音频，纯中文音源则英文怪 | 原生支持 |
| 控制稳定性 | `cfg_value` | `temperature` + `top_p` + `seed` |
| 速度 | GPU ~3s/句 | GPU ~23s/句 |
| quality | 48kHz | 44kHz |

## 稳定推理推荐参数

**VoxCPM2:**
```json
{"text": "你好", "cfg_value": 3.0, "inference_timesteps": 15, "seed": 42}
```

**Fish-Speech:**
```json
{"text": "你好", "seed": 42, "temperature": 0.3, "top_p": 0.5, "repetition_penalty": 1.0}
```
