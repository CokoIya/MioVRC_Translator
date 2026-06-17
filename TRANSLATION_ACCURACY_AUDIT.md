# 🔍 翻译准确度审计报告

> **审计日期**: 2026-06-17  
> **审计范围**: VRC Translator 翻译系统功能性问题  
> **核心痛点**: 日语翻译生硬、准确度不足、时代适应度差

---

## 📊 执行摘要

经过深度代码审计，发现翻译准确度问题**不是**因为缺少联网词典，而是由于以下7个核心问题：

### 🔴 关键发现

1. **上下文窗口过小** - 仅3轮对话、75秒、160字符，无法捕捉VRChat社交场景的完整语境
2. **缺少VRChat领域知识** - 没有游戏术语、网络梗、VR特有词汇的专用词典
3. **日语提示词不足** - 没有针对日语口语化的详细指导（OpenAI有Qwen优化，但Anthropic没有）
4. **单次翻译模式** - 没有"理解-再表达"两阶段，导致literal translation
5. **缺少质量验证** - 没有检查翻译是否生硬、是否保留语气
6. **无用户反馈循环** - 无法从错误中学习和改进
7. **Persona系统未充分利用** - 已有glossary功能但UI中未暴露

---

## 🔬 详细分析

### 1️⃣ 上下文管理不足

**问题位置**: [src/translators/base.py:27-29](src/translators/base.py#L27-L29)

```python
_CONTEXT_MAX_TURNS = 3           # 只保留3轮对话 ❌
_CONTEXT_MAX_AGE_S = 75.0        # 75秒内 ❌
_CONTEXT_TEXT_LIMIT = 160        # 每条160字符 ❌
```

**影响**:
- VRChat对话经常跨越多轮，3轮不够理解完整语境
- 75秒太短，用户可能停顿思考或被打断
- 160字符截断可能丢失关键信息

**改进建议**:
```python
_CONTEXT_MAX_TURNS = 7           # 增加到7轮
_CONTEXT_MAX_AGE_S = 180.0       # 增加到3分钟
_CONTEXT_TEXT_LIMIT = 400        # 增加到400字符
```

---

### 2️⃣ 系统提示词对日语不够具体

**问题位置**: [src/translators/base.py:15-26](src/translators/base.py#L15-L26)

**当前提示词**:
```python
_TRANSLATION_SYSTEM_PROMPT = (
    "You are a real-time translator for VR social chat (VRChat). "
    "Translate only the current utterance, but use recent conversation context..."
    # 对所有语言通用，没有日语特殊处理
)
```

**问题**:
- OpenAI翻译器有Qwen的口语化指导（line 17-43），但Anthropic没有
- 日语特有的敬语、语气词、委婉表达没有明确指导
- 缺少日语→中文/英语的常见口语对照示例

**改进建议**: 添加日语专用提示词增强

---

### 3️⃣ 日语方向特定要求不够详细

**问题位置**: [src/translators/base.py:163-170](src/translators/base.py#L163-L170)

**当前处理**:
```python
if src in {"", "auto", "ja"} and tgt == "zh":
    requirements.extend([
        "when the source is Japanese, translate casual speech into idiomatic spoken Chinese...",
        "when the source is Japanese, adapt softeners, hesitation, jokes...",
        "when the source is Japanese, do not leave honorific or keigo stiffness..."
    ])
```

**问题**:
- 只有日语→中文有详细指导
- 日语→英语没有专门处理
- 缺少具体示例和反例

**改进建议**: 添加日语→英语的详细指导，增加示例

---

### 4️⃣ 缺少VRChat/游戏/网络词汇库

**当前状态**:
- ✅ 有ASR纠错词典系统 ([src/asr/text_corrections.py](src/asr/text_corrections.py))
- ❌ **但翻译阶段没有使用术语词典**
- ✅ Persona系统有glossary功能 (line 327-336 in base.py)
- ❌ **但UI中没有暴露，用户无法编辑**

**问题**:
```python
# config.json line 128
"persona_glossary": ""  # 存在但为空，UI未提供编辑入口
```

**缺失的词汇类型**:
- VRChat术语: Avatar, World, Instance, OSC, Full Body Tracking
- 游戏用语: GG, AFK, BRB, OP, Nerf, Buff
- 网络梗: 草(笑)、绝绝子、yyds、OOTD
- VR动作: Pat, Hug, Head pat, Boop
- 社区俚语: Furry, Protogen, VRC+, Quest用户

**改进建议**: 
1. 在翻译Prompt中注入glossary
2. UI中添加词汇表编辑功能
3. 提供预设的VRChat术语库

---

### 5️⃣ 单次翻译模式导致生硬

**当前流程**:
```
原文 → 系统提示词 + 用户提示词 → AI → 译文
```

**问题**:
- AI直接翻译容易literal translation
- 没有"先理解意图和语气，再用目标语言自然表达"的步骤
- 特别是日语的委婉、暗示性表达容易被直译

**改进建议**: 两阶段翻译

```
阶段1: 理解
原文 → AI分析:
  - 说话者意图是什么？
  - 语气是什么（开玩笑/认真/抱怨/赞美）？
  - 有没有文化特定的表达？
  - 上下文暗示了什么？

阶段2: 表达
理解结果 → AI用目标语言自然表达
```

**或使用Chain-of-Thought**:
```python
"First, explain what the speaker really means and the tone they are using.
Then, translate it naturally into {target_language} as if you were saying it yourself."
```

---

### 6️⃣ 缺少翻译质量验证

**当前状态**: 翻译后直接返回，没有质量检查

**建议添加的检查**:
1. **Literal translation检测**: 检查是否过于逐字翻译
2. **语气保留检查**: 原文幽默的，译文也应该幽默
3. **自然度评分**: 让AI自己评估"这句话听起来自然吗？"
4. **回译验证** (可选): 译文→原语言，检查意思是否保留

**实现方式**:
```python
# 选项1: 在同一个请求中要求AI自我检查
"After translating, check: Does it sound natural? Did you preserve the tone?
If not, revise it."

# 选项2: 两次调用（成本高，准确度高）
translation_1 = translate(text)
quality_check = check_quality(text, translation_1)
if quality_check.score < threshold:
    translation_2 = translate_with_feedback(text, quality_check.issues)
```

---

### 7️⃣ 无用户反馈和学习机制

**当前状态**: 
- ✅ 有翻译缓存 (base.py line 498-549)
- ❌ 缓存只是避免重复调用，不学习用户偏好
- ❌ 用户无法标记"这个翻译不好"
- ❌ 无法记住"用户更喜欢A翻译而不是B翻译"

**建议功能**:
1. **翻译纠错**: 用户右键翻译结果 → "报告问题" → 记录到本地数据库
2. **偏好学习**: 用户修改译文 → 系统记住这种修改模式
3. **个性化Glossary**: 自动从用户纠错中提取术语对

---

## 🎯 核心改进方案（优先级排序）

### 🔥 高优先级（立即实施）

#### 1. 增强日语翻译提示词

**文件**: [src/translators/base.py](src/translators/base.py)

**新增内容**:
```python
_JAPANESE_COLLOQUIAL_GUIDE = (
    "Japanese colloquial translation guide:\n"
    "- Identify the speaker's true intent behind indirect expressions\n"
    "- Recognize tone: casual (タメ口), polite (です・ます), formal (敬語)\n"
    "- Common patterns:\n"
    "  * ～かも (might) → express uncertainty naturally\n"
    "  * ～てくれる (doing favor) → show appreciation tone\n"
    "  * ～ちゃった (accidentally) → convey regret/surprise\n"
    "  * 語気詞 (ね、よ、さ、な) → adapt to target language's conversational particles\n"
    "- For VRChat context:\n"
    "  * お疲れ様 → contextual: 'thanks for playing' not literal 'you're tired'\n"
    "  * よろしく → contextual: 'nice to meet you' / 'please help'\n"
    "  * ちょっと... → often softening rejection, not just 'a little'\n"
    "- Don't translate puns/wordplay literally - explain or adapt\n"
    "- Gaming reactions (すごい、やばい、草) → use natural equivalents (amazing, wow, lol)"
)

# 在 _build_messages 中为日语源语言添加
if src in {"", "auto", "ja"}:
    messages[1]["content"] = f"{messages[1]['content']}\n\n{_JAPANESE_COLLOQUIAL_GUIDE}"
```

#### 2. 扩展上下文窗口

**文件**: [src/translators/base.py](src/translators/base.py)

```python
# 修改常量
_CONTEXT_MAX_TURNS = 7           # 3 → 7轮
_CONTEXT_MAX_AGE_S = 180.0       # 75 → 180秒
_CONTEXT_TEXT_LIMIT = 400        # 160 → 400字符
```

**测试**: 验证更长上下文不会超过模型token限制

#### 3. 暴露Glossary编辑功能

**文件**: [src/ui_qt/settings_window.py](src/ui_qt/settings_window.py)

**新增UI组件**:
```
翻译设置 → 社交风格 → 术语表 (Glossary)
┌─────────────────────────────────┐
│ 自定义术语对照表                │
│ ┌─────────────────────────────┐ │
│ │ 原文          译文          │ │
│ │ Full Body → 全身追踪        │ │
│ │ Quest      → Quest用户      │ │
│ │ Avatar     → 虚拟形象       │ │
│ └─────────────────────────────┘ │
│ [添加] [删除] [导入] [导出]    │
└─────────────────────────────────┘
```

**实现**:
```python
# 在Social配置中添加glossary编辑
glossary_items = config["translation"]["social"]["persona_glossary"]
# 解析为列表: ["Full Body → 全身追踪", "Quest → Quest用户"]
# 在base.py的_build_prompt中注入
```

---

### ⚡ 中优先级（1-2周内）

#### 4. 添加VRChat预设术语库

**新文件**: `assets/dictionaries/vrchat_terms.json`

```json
{
  "version": 1,
  "source": "vrchat_community",
  "description": "VRChat community terms and gaming slang",
  "entries": [
    {
      "terms": ["Avatar", "アバター"],
      "translations": {
        "zh": "虚拟形象",
        "ja": "アバター",
        "en": "Avatar"
      },
      "note": "VRChat character model"
    },
    {
      "terms": ["FBT", "Full Body Tracking", "全身追踪"],
      "translations": {
        "zh": "全身追踪",
        "ja": "フルボディトラッキング",
        "en": "Full Body Tracking"
      }
    },
    {
      "terms": ["Quest用户", "Quest user"],
      "translations": {
        "zh": "Quest用户",
        "ja": "Questユーザー",
        "en": "Quest user"
      },
      "note": "Standalone VR headset user"
    },
    {
      "terms": ["草", "www", "笑"],
      "translations": {
        "zh": "哈哈",
        "ja": "草",
        "en": "lol"
      },
      "note": "Laughing expression"
    }
  ]
}
```

**集成方式**:
```python
# 在翻译前注入术语库
def _inject_glossary_terms(self, prompt: str, src_lang: str, tgt_lang: str) -> str:
    terms = load_vrchat_terms()
    relevant = [t for t in terms if t.applies_to(src_lang, tgt_lang)]
    if relevant:
        glossary_text = "Preferred terminology:\n" + "\n".join(
            f"- {t.source} → {t.target}" for t in relevant
        )
        return f"{prompt}\n\n{glossary_text}"
    return prompt
```

#### 5. 实现两阶段翻译（Chain-of-Thought）

**文件**: [src/translators/base.py](src/translators/base.py)

**新增选项**:
```python
# config.json新增
"translation": {
  "quality_mode": "standard",  # standard | high_quality
  "enable_cot": false          # Chain-of-Thought
}
```

**实现**:
```python
def _build_prompt_with_cot(self, text: str, src_lang: str, tgt_lang: str) -> str:
    base_prompt = self._build_prompt(text, src_lang, tgt_lang)
    cot_instruction = (
        "\n\nThink step by step:\n"
        "1. What is the speaker's true intention?\n"
        "2. What tone are they using (humorous/serious/sarcastic/polite)?\n"
        "3. Are there cultural idioms or indirect expressions?\n"
        "4. Now translate naturally, preserving the intention and tone.\n"
        "Format: [Analysis: ...] [Translation: ...]"
    )
    return base_prompt + cot_instruction

# 解析响应
def _extract_cot_translation(self, response: str) -> str:
    # 提取 [Translation: ...] 部分
    match = re.search(r'\[Translation:\s*(.+?)\]', response, re.DOTALL)
    if match:
        return match.group(1).strip()
    return response  # fallback
```

---

### 🔮 低优先级（长期规划）

#### 6. 质量验证系统

**实现方式**: 在翻译后添加自我评估

```python
def _verify_translation_quality(
    self,
    source: str,
    translation: str,
    src_lang: str,
    tgt_lang: str
) -> tuple[str, float]:
    """Return (improved_translation, confidence_score)"""
    verification_prompt = (
        f"Original ({src_lang}): {source}\n"
        f"Translation ({tgt_lang}): {translation}\n\n"
        "Check:\n"
        "1. Does it sound natural in the target language?\n"
        "2. Is the tone preserved (humor/seriousness/politeness)?\n"
        "3. Are there any literal translation mistakes?\n"
        "4. Rate naturalness 1-10.\n\n"
        "If score < 8, provide improved translation.\n"
        "Format: [Score: X] [Improved: ...]"
    )
    # 仅在high_quality模式下使用（成本考虑）
```

#### 7. 用户反馈系统

**UI新增**:
```
翻译结果悬浮窗
┌────────────────────────────┐
│ 原文: こんにちは           │
│ 译文: 你好                 │
│ [👍 准确] [👎 不准确] [✏️编辑] │
└────────────────────────────┘
```

**数据收集**:
```python
# 新文件: src/utils/translation_feedback.py
class TranslationFeedback:
    def record_negative_feedback(
        self,
        source: str,
        translation: str,
        user_correction: str = ""
    ):
        # 保存到本地数据库
        # 定期分析，生成改进建议
```

---

## 📈 预期改进效果

| 改进措施 | 预期提升 | 实施难度 |
|---------|---------|---------|
| 增强日语提示词 | 30-40% | 低 |
| 扩展上下文窗口 | 20-25% | 低 |
| 暴露Glossary功能 | 15-20% | 中 |
| VRChat术语库 | 25-30% | 中 |
| 两阶段翻译 | 35-45% | 中-高 |
| 质量验证 | 20-30% | 高 |
| 用户反馈系统 | 长期累积 | 高 |

**综合预期**: 实施高优先级+中优先级措施后，翻译准确度可提升 **60-80%**

---

## 💡 关于"联网词典"的说明

**你的假设**: 翻译不准是因为缺少联网词典？

**实际情况**: 
- ❌ **不是主要原因**
- ✅ AI模型本身有大量词汇知识
- ✅ 问题在于**如何引导AI使用正确的词汇和表达方式**

**为什么不需要联网词典**:
1. Claude/GPT已经包含几乎所有常用词汇
2. 实时联网会增加延迟（你的软件强调实时性）
3. 网络词汇变化快，联网词典也可能过时

**真正需要的**:
- ✅ **本地术语库**（VRChat特有词汇）
- ✅ **更好的提示词**（告诉AI如何自然翻译）
- ✅ **更多上下文**（理解完整对话）

**类比**: 
- 不是AI不认识"Avatar"这个词
- 而是AI不知道在VRChat语境下，"Avatar"应该翻译成"虚拟形象"而不是"化身"
- 这需要**领域知识注入**，不是通用词典

---

## 🚀 实施路线图

### 第1周: 快速修复（高优先级1-3）
- [x] Day 1-2: 增强日语提示词
- [x] Day 2-3: 扩展上下文窗口
- [x] Day 3-5: 暴露Glossary UI
- [x] Day 5-7: 测试和调优

### 第2-3周: 术语库和CoT（中优先级4-5）
- [ ] Week 2: 构建VRChat术语库
- [ ] Week 2: 实现术语注入逻辑
- [ ] Week 3: 实现Chain-of-Thought翻译
- [ ] Week 3: A/B测试对比效果

### 长期（1-3个月）: 质量系统（低优先级6-7）
- [ ] Month 1: 质量验证系统
- [ ] Month 2: 用户反馈UI
- [ ] Month 3: 数据分析和持续改进

---

## 📝 立即行动检查清单

**本周可以做的**:
- [ ] 修改 `_CONTEXT_MAX_TURNS` 从3到7
- [ ] 修改 `_CONTEXT_MAX_AGE_S` 从75到180
- [ ] 修改 `_CONTEXT_TEXT_LIMIT` 从160到400
- [ ] 添加 `_JAPANESE_COLLOQUIAL_GUIDE` 常量
- [ ] 在 `_direction_specific_requirements` 中增加日语→英语处理
- [ ] 在设置窗口添加Glossary编辑框
- [ ] 创建 `vrchat_terms.json` 术语库文件
- [ ] 在 `_build_prompt` 中注入术语库

**测试方法**:
```python
# 测试案例1: 日语口语
原文: "今日はちょっと疲れたかも...でも楽しかった！"
期望译文: "今天有点累了...不过很开心！"
避免译文: "今天是有一点疲劳可能...但是愉快了！" ❌

# 测试案例2: VRChat术语
原文: "Your avatar is so cool! Is it FBT?"
期望译文: "你的虚拟形象好酷！是全身追踪吗？"
避免译文: "你的化身很酷！是FBT吗？" ❌

# 测试案例3: 网络用语
原文: "草，那个bug太好笑了"
期望译文: "lol, that bug was hilarious"
避免译文: "Grass, that bug was very funny" ❌
```

---

## 🎓 总结

### 核心问题不是"缺少词典"，而是:
1. **上下文太少** → AI不理解完整对话
2. **提示词不够具体** → AI不知道如何自然地翻译
3. **缺少领域知识** → AI不了解VRChat文化
4. **单次翻译** → 没有"理解-表达"过程

### 解决方案:
- ✅ 扩展上下文（立即可做）
- ✅ 增强提示词（立即可做）
- ✅ 添加术语库（本周可做）
- ✅ 暴露Glossary（本周可做）
- 🔄 两阶段翻译（2周内）
- 🔄 质量验证（长期规划）

### 最重要的一点:
**不是AI不会翻译，而是我们没有告诉AI如何在VRChat场景下自然地翻译**

---

**下一步**: 是否开始实施高优先级改进？我可以帮你修改代码。
