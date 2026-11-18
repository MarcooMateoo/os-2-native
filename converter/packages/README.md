# packages

Code shared by two or more converted apps (for example: back-end platform
helpers for config, auth and audit; web design-system primitives; a mobile
core package). The apps themselves live in `projects/<Project>/converted/`.

Empty on purpose. Move code here from `projects/<Project>/converted/` only when a second app
needs it, and give each package its own README, owner and tests. Apps
depend on packages; packages never depend on apps.
