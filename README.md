# opendesign

A Pointman extension that designs pages and app screens from a brief, with
[Open Design](https://github.com/nexu-io/open-design) and Claude Code on a node. The page posts in the
thread as a page card you can open, with a still of it as its picture. To revise a design, ask again with
the project it gave back: the revision is a new version of the same card.

Python (standard library only), run by the node on Python 3.12.

```
pointman add opendesign
```

## What it needs

- **Open Design,** a built checkout of [nexu-io/open-design](https://github.com/nexu-io/open-design)
  (it has `apps/daemon/dist/cli.js`), at `~/open-design` or the folder the `path` setting names.
- **Node.js 24** to run Open Design's daemon (`node` on the PATH, or the `node` setting).
- **Claude Code** (`claude`), signed in, on the PATH or in one of its usual places.
- **On macOS, for the still:** the Swift compiler (`swiftc`, from Xcode or its Command Line Tools). The
  still is a WebKit picture of the page with its scripts run, built once from `snap.swift`. Elsewhere,
  or with `still = false`, the node's own thumbnail stands in.

## Commands

Agents get it as the `opendesign_make` tool, or call it on a machine with `create_job`.

| Kind | Params | What |
|---|---|---|
| `opendesign.make` | `brief`, `design_md?`, `project?`, `thread_id?` | A design from a brief, as you'd brief a designer, following `design_md` (a DESIGN.md's text) if given. A run takes about 15 minutes, with its progress on the request. Cancellable |

- **The result** is the page (`index.html`, with whatever it uses from its folder) and its still, and
  `{project, run, revised, seconds}`.
- **Revising:** pass the `project` it gave back, with a new brief (and a new `design_md` if the design
  system changed). The same Open Design project is revised and published at the same path,
  `opendesign/<project>/index.html`.

## How it runs

- **Its own daemon:** started on first use, on a free port of its own, with its data in the extension's
  state folder. So it never touches another Open Design on the machine. It stops when the extension does,
  along with the runs it started, and one left behind by a crash is stopped at the next start.
- **The node decides how Claude Code runs:** Open Design starts Claude Code with permissions bypassed.
  The extension gives it a shim instead (`CLAUDE_BIN`), which drops that and runs Claude Code in the
  node's own permission mode (`auto` unless the node sets one) with no MCP servers. So the board's tools
  and tokens stay out of the run.

## Status

`opendesign.status`: whether Open Design is found and its daemon running on this machine, its folder and
its version.

## Settings

Per machine, in `~/.config/pointman/extensions/opendesign/config.toml`, or
`pointman config set node.extensions.opendesign.<key> <value>`:

| Setting | Default | |
|---|---|---|
| `path` | `~/open-design` | Open Design's folder: a built checkout |
| `node` | `node` | The Node.js binary to run its daemon with (it wants Node 24) |
| `plugin` | `od-default` | The Open Design plugin a run uses |
| `still` | `true` | Post a still of the page (macOS) |
| `still_width` | 1440 | The still's width: 1280, 1440 or 1920 (16:10) |
