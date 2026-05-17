# Contributing to Mio RealTime Translator

感谢你对 Mio RealTime Translator 的关注！我们欢迎各种形式的贡献。

Thank you for your interest in contributing to Mio RealTime Translator! We welcome contributions of all kinds.

## 目录 / Table of Contents

- [行为准则 / Code of Conduct](#行为准则--code-of-conduct)
- [如何贡献 / How to Contribute](#如何贡献--how-to-contribute)
- [开发环境设置 / Development Setup](#开发环境设置--development-setup)
- [代码规范 / Code Standards](#代码规范--code-standards)
- [提交流程 / Submission Process](#提交流程--submission-process)
- [报告问题 / Reporting Issues](#报告问题--reporting-issues)

## 行为准则 / Code of Conduct

### 中文

我们致力于为所有人提供友好、安全和包容的环境。请遵守以下准则：

- 尊重不同的观点和经验
- 接受建设性的批评
- 关注对社区最有利的事情
- 对其他社区成员表示同理心

### English

We are committed to providing a friendly, safe, and welcoming environment for all. Please follow these guidelines:

- Be respectful of differing viewpoints and experiences
- Accept constructive criticism gracefully
- Focus on what is best for the community
- Show empathy towards other community members

## 如何贡献 / How to Contribute

### 贡献类型 / Types of Contributions

我们欢迎以下类型的贡献：

We welcome the following types of contributions:

- 🐛 **Bug 修复 / Bug Fixes** - 修复已知问题 / Fix known issues
- ✨ **新功能 / New Features** - 添加新功能 / Add new features
- 📝 **文档改进 / Documentation** - 改进文档 / Improve documentation
- 🌍 **翻译 / Translations** - 添加或改进翻译 / Add or improve translations
- 🧪 **测试 / Tests** - 添加测试用例 / Add test cases
- 🎨 **UI 改进 / UI Improvements** - 改进用户界面 / Improve user interface
- ⚡ **性能优化 / Performance** - 优化性能 / Optimize performance

## 开发环境设置 / Development Setup

### 前置要求 / Prerequisites

- Python 3.10 或更高版本 / Python 3.10 or higher
- Git
- Windows 10/11 (推荐用于完整测试 / recommended for full testing)

### 安装步骤 / Installation Steps

```bash
# 1. Fork 并克隆仓库 / Fork and clone the repository
git clone https://github.com/YOUR_USERNAME/vrc-translator.git
cd vrc-translator

# 2. 创建虚拟环境 / Create virtual environment
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/macOS
source venv/bin/activate

# 3. 安装依赖 / Install dependencies
pip install -r requirements.txt

# 4. 安装测试依赖 / Install test dependencies
pip install -r tests/requirements.txt

# 5. 复制配置文件 / Copy config file
copy config.example.json config.json

# 6. 运行应用 / Run the application
python main.py
```

### 可选：下载模型 / Optional: Download Models

```bash
# 提前下载 ASR 模型 / Pre-download ASR models
python download_models.py
```

## 代码规范 / Code Standards

### Python 代码风格 / Python Code Style

我们遵循 PEP 8 风格指南，但有一些调整：

We follow PEP 8 style guide with some adjustments:

- **缩进 / Indentation**: 4 个空格 / 4 spaces
- **行长度 / Line Length**: 最大 100 字符（推荐 88）/ Max 100 characters (88 recommended)
- **引号 / Quotes**: 优先使用双引号 / Prefer double quotes
- **类型提示 / Type Hints**: 为公共 API 添加类型提示 / Add type hints for public APIs

### 代码质量检查 / Code Quality Checks

```bash
# 运行测试 / Run tests
pytest tests/ -v

# 代码格式化 / Format code (optional)
black src/ tests/

# 类型检查 / Type checking (optional)
mypy src/
```

### 命名约定 / Naming Conventions

- **模块 / Modules**: `lowercase_with_underscores.py`
- **类 / Classes**: `PascalCase`
- **函数 / Functions**: `lowercase_with_underscores()`
- **常量 / Constants**: `UPPERCASE_WITH_UNDERSCORES`
- **私有成员 / Private**: `_leading_underscore`

### 文档字符串 / Docstrings

为公共函数和类添加文档字符串：

Add docstrings for public functions and classes:

```python
def translate_text(text: str, src_lang: str, tgt_lang: str) -> str:
    """Translate text from source language to target language.

    Args:
        text: The text to translate.
        src_lang: Source language code (e.g., 'en', 'zh').
        tgt_lang: Target language code.

    Returns:
        Translated text.

    Raises:
        RuntimeError: If translation fails.
    """
    pass
```

## 提交流程 / Submission Process

### 1. 创建分支 / Create a Branch

```bash
# 从 main 创建新分支 / Create new branch from main
git checkout -b feature/your-feature-name

# 或 / or
git checkout -b fix/bug-description
```

### 分支命名约定 / Branch Naming Convention

- `feature/` - 新功能 / New features
- `fix/` - Bug 修复 / Bug fixes
- `docs/` - 文档更新 / Documentation updates
- `refactor/` - 代码重构 / Code refactoring
- `test/` - 测试相关 / Test-related changes

### 2. 进行更改 / Make Changes

- 保持提交小而专注 / Keep commits small and focused
- 编写清晰的提交消息 / Write clear commit messages
- 添加测试（如果适用）/ Add tests (if applicable)
- 更新文档（如果需要）/ Update documentation (if needed)

### 3. 提交消息格式 / Commit Message Format

```
<type>: <subject>

<body>

<footer>
```

**类型 / Types**:
- `feat`: 新功能 / New feature
- `fix`: Bug 修复 / Bug fix
- `docs`: 文档更新 / Documentation
- `style`: 代码格式 / Code formatting
- `refactor`: 重构 / Refactoring
- `test`: 测试 / Tests
- `chore`: 构建/工具 / Build/tooling

**示例 / Example**:
```
feat: add support for Gemini 2.0 Flash model

- Add Gemini 2.0 Flash to model list
- Update default model selection
- Add configuration migration

Closes #123
```

### 4. 运行测试 / Run Tests

```bash
# 运行所有测试 / Run all tests
pytest tests/ -v

# 运行特定测试 / Run specific test
pytest tests/test_config_manager.py -v

# 检查覆盖率 / Check coverage
pytest tests/ --cov=src --cov-report=html
```

### 5. 推送并创建 PR / Push and Create PR

```bash
# 推送分支 / Push branch
git push origin feature/your-feature-name

# 在 GitHub 上创建 Pull Request
# Create Pull Request on GitHub
```

### PR 检查清单 / PR Checklist

在提交 PR 之前，请确保：

Before submitting a PR, ensure:

- [ ] 代码遵循项目风格指南 / Code follows project style guide
- [ ] 添加了必要的测试 / Added necessary tests
- [ ] 所有测试通过 / All tests pass
- [ ] 更新了相关文档 / Updated relevant documentation
- [ ] 提交消息清晰明了 / Commit messages are clear
- [ ] 没有合并冲突 / No merge conflicts
- [ ] PR 描述清楚说明了更改 / PR description clearly explains changes

### PR 描述模板 / PR Description Template

```markdown
## 更改类型 / Type of Change
- [ ] Bug 修复 / Bug fix
- [ ] 新功能 / New feature
- [ ] 文档更新 / Documentation update
- [ ] 性能优化 / Performance improvement
- [ ] 其他 / Other

## 描述 / Description
简要描述你的更改...
Brief description of your changes...

## 相关 Issue / Related Issues
Closes #123

## 测试 / Testing
描述你如何测试这些更改...
Describe how you tested these changes...

## 截图 / Screenshots (如果适用 / if applicable)
```

## 报告问题 / Reporting Issues

### Bug 报告 / Bug Reports

提交 Bug 报告时，请包含：

When reporting bugs, please include:

- **环境信息 / Environment**: OS, Python 版本, 应用版本
- **重现步骤 / Steps to Reproduce**: 详细的重现步骤
- **期望行为 / Expected Behavior**: 你期望发生什么
- **实际行为 / Actual Behavior**: 实际发生了什么
- **日志 / Logs**: 相关的错误日志
- **截图 / Screenshots**: 如果适用

### 功能请求 / Feature Requests

提交功能请求时，请说明：

When requesting features, please explain:

- **用例 / Use Case**: 为什么需要这个功能
- **建议方案 / Proposed Solution**: 你建议如何实现
- **替代方案 / Alternatives**: 你考虑过的其他方案
- **优先级 / Priority**: 这个功能对你有多重要

## 翻译贡献 / Translation Contributions

### 添加新语言 / Adding a New Language

1. 在 `src/utils/i18n.py` 中添加翻译字符串
2. 在 `src/ui/` 中的 UI 文件中添加翻译
3. 测试所有 UI 元素显示正确
4. 创建 `docs/README.{lang}.md`

### 改进现有翻译 / Improving Existing Translations

- 检查 `src/utils/i18n.py` 中的翻译
- 确保翻译自然、准确
- 保持一致的术语使用

## 代码审查流程 / Code Review Process

### 审查标准 / Review Criteria

我们会检查：

We check for:

- **功能正确性 / Functionality**: 代码是否按预期工作
- **代码质量 / Code Quality**: 是否遵循最佳实践
- **测试覆盖 / Test Coverage**: 是否有足够的测试
- **文档 / Documentation**: 是否有必要的文档
- **性能 / Performance**: 是否有性能问题
- **安全性 / Security**: 是否有安全隐患

### 审查时间 / Review Timeline

- 我们会尽快审查 PR，通常在 1-3 天内
- 复杂的 PR 可能需要更长时间
- 如果超过一周没有回复，请在 PR 中留言提醒

We aim to review PRs quickly, usually within 1-3 days
- Complex PRs may take longer
- If no response after a week, please comment on the PR

## 社区 / Community

### 获取帮助 / Getting Help

- **GitHub Issues**: 报告 Bug 或请求功能
- **GitHub Discussions**: 提问和讨论
- **QQ 群**: 1PThd3QBTS
- **LINE 群**: [链接](https://line.me/ti/g2/uLhASjhfQcsd5tYsEpFr8GWsCcuYVIq1I6iGwA)

### 保持联系 / Stay Connected

- ⭐ Star 项目以获取更新
- 👀 Watch 项目以接收通知
- 🍴 Fork 项目开始贡献

## 许可证 / License

通过贡献代码，你同意你的贡献将在 MIT 许可证下发布。

By contributing, you agree that your contributions will be licensed under the MIT License.

## 致谢 / Acknowledgments

感谢所有贡献者！你们的贡献让这个项目变得更好。

Thank you to all contributors! Your contributions make this project better.

---

**问题？/ Questions?**

如果你有任何问题，请随时在 GitHub Issues 中提问。

If you have any questions, feel free to ask in GitHub Issues.
