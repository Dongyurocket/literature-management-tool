# Agent Guide

## Default Collaboration Style
- Prefer technical English when structuring analysis, plans, debugging steps, TODOs, and implementation breakdowns.
- Use Chinese for user-facing interaction by default unless the user explicitly asks for another language.
- Keep technical identifiers, API names, library names, protocol names, and file-format names in English.

## Comment Policy
- Write code comments in Chinese when comments are necessary.
- Do not add comments for obvious code.
- Use ASCII-style section comments only.
- Avoid Unicode box-drawing characters, emoji, or decorative separators in code comments.

## ASCII Comment Examples
- Python
  - `# ===== 模块入口 =====`
  - `# ----- 参数解析 -----`
- C / C++ / Java / JavaScript
  - `// ===== 主流程 =====`
  - `// ----- 几何构建 -----`
- TOML / YAML / Shell
  - `# ===== 配置说明 =====`
  - `# ----- 环境变量 -----`

## Editing Defaults
- Prefer ASCII when creating or editing files unless Chinese content is clearly more appropriate.
- Preserve an existing file's local style when that file already follows a stable convention.
- Keep edits minimal, explicit, and easy to review.

## Communication Defaults
- Answer questions in Chinese with a direct, collaborative tone.
- Keep explanations practical and implementation-oriented.
- When presenting commands, code paths, or config keys, preserve the original English text exactly.

## Repo-Specific Preference
- When adding project documentation, prefer Chinese explanations for overview text.
- When adding examples, use concise headings and readable structure.
- When introducing new comments in source files, follow the Chinese plus ASCII-section style in this guide.
