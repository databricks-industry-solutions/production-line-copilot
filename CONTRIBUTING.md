### Before you open a pull request

Run the gate:

```bash
./scripts/check.sh          # ruff, pytest, tsc, eslint, vitest
./scripts/check.sh python   # or just one half
```

`.github/workflows/tests.yml` runs this same script, so passing locally and
passing in CI mean the same thing. Run it locally regardless: some GitHub
Enterprise organisations disable hosted runners, and where they are disabled the
workflow cannot start — it reports a failure without having run anything, so a
red check there does not necessarily mean a broken change, and a missing check
does not mean a safe one.

Since remembering to run it is the weakest link — and on a repository with
hosted runners disabled it is the only link — you can have git run it for you:

```bash
./scripts/hooks/install.sh   # symlinks scripts/hooks/pre-push into .git/hooks
```

Every push then runs the gate first and aborts if it fails. To push without it,
use `SKIP_CHECKS=1 git push` rather than `git push --no-verify`: the latter
skips *every* hook, including any secret scanning your organisation installs.

The installer changes nothing in your git config, so it is safe alongside
managed hooks that set `core.hooksPath` — those run the repo-local hook before
their own checks.

The Playwright smoke test is not part of the gate, because it boots the real
server against a warehouse and a Lakebase instance. Run `npm run test:smoke`
yourself if you have a workspace and your change touches the UI.

### Publishing a release

The public repository is not a remote of this one. Each release is a single
squashed commit whose tree is a copy of this tree, parented on the previous
public commit, so **everything tracked here is published unless it is named in
`scripts/public-exclude.txt`**. Do not build that commit by hand:

```bash
./scripts/mirror_public.sh --message-file notes.md          # build and verify
./scripts/mirror_public.sh --message-file notes.md --push   # publish it
```

It strips the excluded paths, then refuses to publish a tree that still contains
one, that has a document linking a file it no longer ships, or that cannot build
and pass its checks without them. Without `--push` it stops after verifying and
prints the commit it would publish.

If you add something that must stay internal, add it to
`scripts/public-exclude.txt` — and make sure the public copy still stands up
without it, since the mirror is checked in that state rather than in this one.

### Contributor License Agreement (CLA)

By submitting a contribution to this repository, you certify that:

1. **You have the right to submit the contribution.**  
   You created the code/content yourself, or you have the right to submit it under the project's license.

2. **You grant us a license to use your contribution.**  
   You agree that your contribution will be licensed under the same terms as the rest of this project, and you grant the project maintainers the right to use, modify, and distribute your contribution as part of the project.

3. **You are not submitting confidential or proprietary information.**  
   Your contribution does not include anything you don’t have permission to share publicly.

If you are contributing on behalf of an organization, you confirm that you have the authority to do so. You agree to confirm these terms in your pull request. Any request that does not explicitely accept the terms will be assumed to have accepted. 
