# Screenshot assets

Images referenced from the description of the "rebuild the switcher around
resuming work" pull request. They live on their own branch so the binaries are
never merged into `main`.

Captured against a `vite preview` of the production bundle with every
`/api/v1/**` response stubbed at the network layer — the repo's own Playwright
harness needs Postgres, which the authoring environment had no daemon for. The
"before" images were shot from the pull request's parent commit against the
same fixtures in `created_at` order, which is what the old endpoint returned.

Safe to delete once the pull request is merged.
