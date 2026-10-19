# iOS renders Read passages with its own allowlist renderer

**Status:** accepted (2026-10-19)

The iOS app parses Read passages (untrusted model output, GitHub-Flavored Markdown) with Apple's `swift-markdown`. It draws them with an app-owned SwiftUI renderer that covers only the subset the API documents: headings, lists, emphasis, inline code, code blocks, tables, block quotes and links (`http` and `https` only). A ` ```mermaid ` fence is drawn as a code block. Any other node, such as raw HTML or images, has no renderer and is dropped, so the safety rules hold because of how the renderer is built, not because a library was configured correctly.

**Considered options**
- *swift-markdown-ui:* rejected. It is in maintenance mode, and its safety rules would rest on configuration.
- *Textual* (its successor): rejected. It is pre-1.0 and fetches remote images, with no documented way to turn that off.

**Consequences:** we own a few hundred lines of rendering code, and tables and code blocks need design effort. The parse-and-filter layer has no UI dependencies, so it is unit-tested on Linux.
