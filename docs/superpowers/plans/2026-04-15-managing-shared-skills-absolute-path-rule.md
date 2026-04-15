# Managing Shared Skills Absolute Path Rule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the shared `managing-shared-skills` skill so future shared skills document self-owned script commands with shared-source absolute paths instead of workspace-relative paths.

**Architecture:** Make a single documentation change in `~/.config/shared-skills/managing-shared-skills/SKILL.md`, adding a mandatory rule plus examples that distinguish shared skill source paths from current workspace paths. Verify the wording is precise enough that future edits to shared skills follow the same convention.

**Tech Stack:** Markdown, shared-skills conventions, ripgrep

---

## File Structure

- Modify: `~/.config/shared-skills/managing-shared-skills/SKILL.md` — add the absolute-path rule and concrete examples.

### Task 1: Add the shared-skill absolute-path rule

**Files:**
- Modify: `~/.config/shared-skills/managing-shared-skills/SKILL.md`

- [ ] **Step 1: Insert the new rule into the editing/rules guidance**

```md
### 编辑共享技能

1. **只编辑 `~/.config/shared-skills/` 下的文件**，不要编辑 symlink 目标。
2. 修改后立即生效，无需重新同步（symlink 指向同一文件）。
3. 若脚本中引用了其他共享技能，使用相对于 shared-skills 的路径：
   ```bash
   SHARED_SKILLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
   ```
4. **如果 `SKILL.md` 里的命令直接调用该共享技能自带脚本，必须写共享技能源目录的绝对路径，不要写相对当前工作区的 `scripts/...`。**
   推荐写法：
   ```bash
   python "$HOME/.config/shared-skills/<skill-name>/scripts/<script>.py" ...
   bash "$HOME/.config/shared-skills/<skill-name>/scripts/<script>.sh" ...
   ```
   不推荐写法：
   ```bash
   python scripts/<script>.py ...
   ./scripts/<script>.sh ...
   ```
```

- [ ] **Step 2: Add a matching rule in the final rules section**

```md
8. **共享技能脚本绝对路径**：共享技能 `SKILL.md` 里凡是直接调用本技能自带脚本的命令，必须使用 `~/.config/shared-skills/<skill-name>/...` 形式的绝对路径，避免 Agent 把路径错误解析为当前工作区路径。
```

- [ ] **Step 3: Run grep to verify the new rule text exists**

Run: `rg -n "共享技能脚本绝对路径|不推荐写法|当前工作区路径" ~/.config/shared-skills/managing-shared-skills/SKILL.md`
Expected: matches for the inserted guidance

- [ ] **Step 4: Run a focused readback check**

Run: `sed -n '30,140p' ~/.config/shared-skills/managing-shared-skills/SKILL.md`
Expected: the editing section and rules section both mention absolute shared-skill script paths.

- [ ] **Step 5: Commit**

```bash
git add ~/.config/shared-skills/managing-shared-skills/SKILL.md
git commit -m "docs: require absolute script paths in shared skills"
```
