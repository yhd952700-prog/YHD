# 构建代码审查员

本教程将带你构建一个专业的代码审查智能体，能够自动审查代码质量、发现问题并给出改进建议。

---

## 目标

我们将构建一个代码审查员，它能够：

1. 读取和理解代码
2. 检查代码风格和规范
3. 发现潜在的 bug 和安全问题
4. 评估性能和可维护性
5. 生成结构化的审查报告
6. 给出具体的改进建议

---

## 前置条件

- LiuHao AI OS 服务运行中
- Python SDK 已安装
- 待审查的代码（本地文件或 Git 仓库）

---

## 第一步：设计代码审查员

### 1.1 角色定位

代码审查员需要具备：
- 多种编程语言的理解能力
- 代码质量评估能力
- 安全漏洞识别能力
- 结构化反馈能力

### 1.2 创建智能体

创建 `code_reviewer_agent.py`：

```python
"""
代码审查员智能体
"""
from liuhao import LiuHaoClient

client = LiuHaoClient(base_url="http://127.0.0.1:8080")

system_prompt = """你是一名资深软件工程师和代码审查专家，名叫"CodeReviewer"。

你的专长：
- Python、JavaScript/TypeScript、Go、Java 等多种编程语言
- 代码质量评估
- 安全漏洞发现
- 性能优化建议
- 最佳实践推荐
- 架构设计评审

审查维度：
1. 代码正确性（Correctness）
   - 逻辑是否正确
   - 边界条件处理
   - 错误处理
   - 并发安全

2. 代码风格（Style）
   - 是否符合语言规范
   - 命名是否清晰
   - 代码格式是否一致
   - 注释是否充分

3. 安全性（Security）
   - 输入验证
   - 注入风险
   - 敏感信息处理
   - 权限控制

4. 性能（Performance）
   - 算法效率
   - 资源使用
   - 内存泄漏风险
   - 缓存使用

5. 可维护性（Maintainability）
   - 代码复杂度
   - 重复代码
   - 模块划分
   - 测试覆盖

6. 最佳实践（Best Practices）
   - 设计模式使用
   - 错误处理模式
   - 测试建议
   - 文档建议

输出格式：
请使用结构化的 Markdown 格式输出审查报告，包含以下部分：

# 代码审查报告

## 总体评价
（总体评分和简要评价）

## 问题列表
（按严重程度排序的问题列表）

### 严重问题（Critical）
### 重要问题（Major）
### 一般问题（Minor）
### 建议优化（Info）

## 详细审查
（按文件/模块的详细审查意见）

## 改进建议
（具体的改进建议和代码示例）

## 评分
- 正确性: X/10
- 安全性: X/10
- 性能: X/10
- 可维护性: X/10
- 综合评分: X/10

每个问题请包含：
- 位置（文件:行号）
- 问题描述
- 严重程度
- 改进建议
- 代码示例（如有）

请保持专业、客观、建设性的语气。"""

# 创建代码审查员
agent = client.agents.create(
    name="CodeReviewer",
    role="critic",
    description="专业代码审查专家，擅长发现代码问题和质量改进",
    config={
        "model": "qwen2.5:7b",
        "temperature": 0.2,  # 低温度，输出更确定
        "max_tokens": 8192,
        "system_prompt": system_prompt,
        "tools": [
            "file_reader.read",
            "file_reader.list_dir",
            "git.diff",
            "git.show"
        ],
        "memory": {
            "enabled": True,
            "semantic_memory_enabled": True
        }
    }
)

print(f"✅ 代码审查员创建成功！")
print(f"   ID: {agent.id}")

with open("code_reviewer_id.txt", "w") as f:
    f.write(agent.id)
```

---

## 第二步：构建审查工作流

### 2.1 单文件审查

创建 `code_review.py`：

```python
"""
代码审查工作流
"""
import os
from liuhao import LiuHaoClient

client = LiuHaoClient(base_url="http://127.0.0.1:8080")

with open("code_reviewer_id.txt", "r") as f:
    agent_id = f.read().strip()


def review_file(file_path: str, output_file: str = None) -> str:
    """
    审查单个文件
    
    Args:
        file_path: 代码文件路径
        output_file: 报告输出路径
    
    Returns:
        审查报告
    """
    print(f"🔍 正在审查：{file_path}")
    
    # 读取文件内容
    with open(file_path, "r", encoding="utf-8") as f:
        code_content = f.read()
    
    # 获取文件扩展名判断语言
    ext = os.path.splitext(file_path)[1]
    language_map = {
        ".py": "Python",
        ".js": "JavaScript",
        ".ts": "TypeScript",
        ".go": "Go",
        ".java": "Java",
        ".cpp": "C++",
        ".c": "C",
        ".rs": "Rust"
    }
    language = language_map.get(ext, "Unknown")
    
    # 构建审查任务
    task = f"""请对以下 {language} 代码进行全面审查：

文件：{file_path}

代码内容：
```{language.lower()}
{code_content}
```

请按照你的审查标准进行全面分析，重点关注：
1. 逻辑正确性
2. 潜在 bug
3. 安全漏洞
4. 性能问题
5. 代码质量和可维护性
6. 最佳实践遵循情况

给出具体的改进建议和代码示例。"""
    
    # 执行审查
    print("⏳ 分析中，请稍候...\n")
    
    result = client.agents.execute(
        agent_id=agent_id,
        task=task,
        context={
            "file_path": file_path,
            "language": language
        },
        options={
            "max_steps": 10,
            "timeout_seconds": 300
        }
    )
    
    report = result.output
    
    # 输出报告
    if output_file is None:
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        output_file = f"review_{base_name}.md"
    
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"# 代码审查报告：{os.path.basename(file_path)}\n\n")
        f.write(f"**文件路径**: `{file_path}`  \n")
        f.write(f"**编程语言**: {language}  \n")
        f.write(f"**审查时间**: 自动生成  \n\n")
        f.write("---\n\n")
        f.write(report)
    
    print(f"✅ 审查完成！")
    print(f"💾 报告已保存：{output_file}")
    
    return report


def review_directory(dir_path: str, output_file: str = None) -> str:
    """
    审查整个目录下的代码
    
    Args:
        dir_path: 目录路径
        output_file: 报告输出路径
    """
    print(f"📂 审查目录：{dir_path}")
    
    # 收集代码文件
    code_extensions = {".py", ".js", ".ts", ".go", ".java", ".cpp", ".c", ".rs"}
    code_files = []
    
    for root, dirs, files in os.walk(dir_path):
        # 跳过一些目录
        dirs[:] = [d for d in dirs if d not in 
                   ["node_modules", "__pycache__", ".git", "dist", "build"]]
        
        for file in files:
            ext = os.path.splitext(file)[1]
            if ext in code_extensions:
                code_files.append(os.path.join(root, file))
    
    print(f"📄 找到 {len(code_files)} 个代码文件")
    
    if not code_files:
        print("❌ 没有找到代码文件")
        return ""
    
    # 逐个审查
    all_reports = []
    
    for i, file_path in enumerate(code_files, 1):
        print(f"\n[{i}/{len(code_files)}] 审查 {file_path}")
        try:
            report = review_file(file_path)
            all_reports.append(f"## {file_path}\n\n{report}")
        except Exception as e:
            print(f"⚠️  审查失败：{e}")
            all_reports.append(f"## {file_path}\n\n审查失败：{e}")
    
    # 生成汇总报告
    if output_file is None:
        output_file = "code_review_report.md"
    
    summary = f"# 代码审查汇总报告\n\n"
    summary += f"**审查目录**: `{dir_path}`  \n"
    summary += f"**文件数量**: {len(code_files)}  \n"
    summary += f"**审查时间**: 自动生成  \n\n"
    summary += "---\n\n"
    summary += "\n\n---\n\n".join(all_reports)
    
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(summary)
    
    print(f"\n✅ 全部审查完成！汇总报告：{output_file}")
    
    return summary


# 主函数
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python code_review.py <文件或目录路径>")
        print("\n示例:")
        print("  python code_review.py my_code.py")
        print("  python code_review.py ./src/")
        sys.exit(1)
    
    path = sys.argv[1]
    
    if os.path.isfile(path):
        review_file(path)
    elif os.path.isdir(path):
        review_directory(path)
    else:
        print(f"❌ 路径不存在：{path}")
        sys.exit(1)
```

### 2.2 运行审查

```bash
# 审查单个文件
python code_review.py my_script.py

# 审查整个目录
python code_review.py ./src/
```

---

## 第三步：Git 差异审查

审查 Git 提交的变更，更适合 Code Review 场景。

### 3.1 Git Diff 审查

```python
"""
Git 提交审查
"""
import subprocess
from liuhao import LiuHaoClient

client = LiuHaoClient(base_url="http://127.0.0.1:8080")

with open("code_reviewer_id.txt", "r") as f:
    agent_id = f.read().strip()


def review_git_diff(repo_path: str, commit_hash: str = None, 
                    base_branch: str = "main") -> str:
    """
    审查 Git 变更
    
    Args:
        repo_path: 仓库路径
        commit_hash: 要审查的提交（None 则审查未提交的变更）
        base_branch: 对比的基准分支
    """
    print(f"🔍 审查 Git 变更：{repo_path}")
    
    # 获取 diff
    if commit_hash:
        # 审查特定提交
        cmd = ["git", "show", commit_hash, "--stat"]
    else:
        # 审查未提交的变更
        cmd = ["git", "diff", "HEAD"]
    
    result = subprocess.run(
        cmd, cwd=repo_path, capture_output=True, text=True
    )
    
    if result.returncode != 0:
        print(f"❌ Git 命令失败：{result.stderr}")
        return ""
    
    diff_content = result.stdout
    
    if not diff_content.strip():
        print("ℹ️  没有变更需要审查")
        return ""
    
    # 获取变更统计
    stat_result = subprocess.run(
        ["git", "diff", "--stat"], cwd=repo_path,
        capture_output=True, text=True
    )
    
    print(f"📊 变更统计：\n{stat_result.stdout}")
    
    # 构建审查任务
    task = f"""请审查以下代码变更：

变更统计：
```
{stat_result.stdout}
```

变更详情：
```diff
{diff_content}
```

请从以下方面进行审查：
1. 变更是否合理，是否达到预期目的
2. 是否引入了新的 bug 或问题
3. 代码质量是否符合标准
4. 是否有安全隐患
5. 是否需要补充测试
6. 命名和注释是否恰当

请按文件组织审查意见，重点关注重要问题。"""
    
    print("⏳ 分析中...\n")
    
    result = client.agents.execute(
        agent_id=agent_id,
        task=task,
        options={
            "max_steps": 15,
            "timeout_seconds": 300
        }
    )
    
    report = result.output
    print(report)
    
    # 保存报告
    output_file = "git_diff_review.md"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("# Git 变更审查报告\n\n")
        f.write(f"**仓库**: `{repo_path}`  \n")
        if commit_hash:
            f.write(f"**提交**: `{commit_hash}`  \n")
        f.write("\n---\n\n")
        f.write(report)
    
    print(f"\n💾 报告已保存：{output_file}")
    
    return report


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python git_review.py <仓库路径> [提交哈希]")
        print("\n示例:")
        print("  python git_review.py ./my-project/")
        print("  python git_review.py ./my-project/ abc123def")
        sys.exit(1)
    
    repo_path = sys.argv[1]
    commit_hash = sys.argv[2] if len(sys.argv) > 2 else None
    
    review_git_diff(repo_path, commit_hash)
```

---

## 第四步：审查质量提升

### 4.1 自定义审查规则

你可以根据团队的编码规范自定义审查标准：

```python
custom_rules = """
额外的团队规范：
1. Python 代码必须遵循 PEP 8
2. 函数长度不超过 50 行
3. 所有公共函数必须有 docstring
4. 异常处理必须具体，不使用裸 except
5. 日志使用结构化日志格式
6. 配置必须通过环境变量传入，不能硬编码
"""

task = f"""请审查以下代码...

{custom_rules}

除了标准审查外，请特别检查是否符合以上团队规范。"""
```

### 4.2 安全专项审查

针对安全的深度审查：

```python
task = """请对以下代码进行安全专项审查，重点关注：

1. 输入验证和注入漏洞（SQL 注入、XSS、命令注入等）
2. 认证和授权问题
3. 敏感信息处理（密码、密钥、个人信息）
4. 不安全的依赖和库
5. 数据泄露风险
6. 业务逻辑漏洞

每个安全问题请标注严重程度（Critical/High/Medium/Low）
和修复建议。"""
```

### 4.3 性能专项审查

针对性能的深度审查：

```python
task = """请对以下代码进行性能专项审查：

1. 时间复杂度分析
2. 空间复杂度分析
3. 潜在的性能瓶颈
4. 可优化的点
5. 算法改进建议
6. 缓存使用建议"""
```

---

## 第五步：集成到 CI/CD

你可以将代码审查集成到 CI/CD 流程中：

### GitHub Actions 示例

```yaml
# .github/workflows/code-review.yml
name: AI Code Review

on:
  pull_request:
    branches: [main]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      
      - name: Install dependencies
        run: |
          pip install liuhao
      
      - name: Run AI Code Review
        env:
          LIUHAO_BASE_URL: ${{ secrets.LIUHAO_BASE_URL }}
          LIUHAO_API_KEY: ${{ secrets.LIUHAO_API_KEY }}
        run: |
          python scripts/code_review.py .
      
      - name: Comment PR
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const report = fs.readFileSync('code_review_report.md', 'utf8');
            github.rest.issues.createComment({
              owner: context.repo.owner,
              repo: context.repo.repo,
              issue_number: context.issue.number,
              body: report
            });
```

---

## 最佳实践

1. **从小处开始**：先用于辅助审查，逐步建立信任
2. **人工复核**：AI 审查不能完全替代人工，重要变更仍需人工 review
3. **持续优化**：根据团队反馈调整审查标准
4. **关注重点**：重点关注安全和正确性问题，风格问题可以用 linter 解决
5. **上下文充分**：给审查员提供足够的项目背景和规范文档

---

## 下一步

- [构建你的第一个智能体](build_your_first_agent.md) - 入门教程
- [构建多智能体系统](build_multi_agent_system.md) - 构建审查团队
- [部署到生产环境](deploy_to_production.md) - 生产部署指南
