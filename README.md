# CopyBot

CopyBot is a tool that automates commit copying from a third-party
repository into Gerrit.  Currently, it's used for:
* Zephyr
* coreboot
* kernel
* ChromeOS Firmware (coreboot & EC milestone waterfall forward-porting and `BRANCH=` tag dispatch)
* AMD Blobs
* Fingerprint synchronization
* Pigweed
* CHRE

[TOC]

## CopyBot vs. Copybara

Google already has very complex infrastructure to copy code from one
place to another called [Copybara].  So why does CopyBot exist?
Copybara works at the "tree level" and does not know how to
cherry-pick code between sources.  Rightfully, adding support for
cherry-picking comes with additional complexities, like figuring out
how to handle merge conflicts.

However, many of our teams need the ability to either maintain patches
on a temporary basis, or land commits in a different order to
integrate dependencies.  Thus, we need the ability to do cherry-picks,
even if it does mean extra complexity.

Should [Copybara] support this in the future in a manner which works
well for CopyBot's users, we should work to deprecate our usages of
CopyBot in favor of unified infrastructure.

## CopyBot's design

CopyBot is a set of Python scripts:

`copybot.py`, `gerrit.py`, `copybot_argparser.py`, and `kernel_cl_dispatch.py`

`copybot.py` is the main entry point and orchestrates copying commits from one
repository to another.  Its general usage is:

```
./copybot.py [options...] <upstream_repo>:<upstream_branch>:<upstream_subtree> <downstream_repo>:<downstream_branch>:<downstream_subtree>
```

The repository configurations can be avoided by using a config file:
```
./copybot.py [options...] --config ./config/path/to/config
```

Run `./copybot.py --help` for a complete list of options supported.

`copybot.py` will then:

1. Discover historical relationships and identify downstream commits that
   already incorporate upstream changes by matching:
   * Exact commit hashes.
   * `GitOrigin-RevId`, `Original-Commit-Id`, or `(cherry picked from commit <sha>)`
     trailers in commit messages.
   * `Change-Id` or `Original-Change-Id` matching on related GoB repositories
     (checking merged history, active pending Gerrit CL stacks, and abandoned CLs).

2. Identify the commits that need to be cherry-picked from the boundary commit up to
   the upstream limit (filtering by file patterns or commit filters as configured).

3. Cherry-pick those commits onto the downstream repo branch or dynamically
   dispatched target branches.

4. Push the changes to Gerrit (or leave them local in `--dry-run` mode).

Use `--dry-run` to test changes without pushing to the downstream repository.

`gerrit.py` encapsulates git operations and GoB/Gerrit interactions.  It contains
the `GitRepo` class (managing clones, fetches, cherry-picks, `merge-base`, `ls-remote`,
and push refspecs) and Gerrit REST API client functions (managing changes, review
labels, topics, hashtags, reviewers, and commit message rewriting).

`copybot_argparser.py` encapsulates argument parsing, type validation, `.ini` config
file loading, and LUCI job generation options.

`kernel_cl_dispatch.py` provides specialized logic for ChromeOS kernel CL dispatching,
parsing `Fixes:` and `Branches:` trailers and resolving target kernel branch groups.

## Using CopyBot

CopyBot is intended to be run at a specified interval as a cron job.  The schedule
can be found in each project's config directory in the `group_config.ini` file.

Your job as a downstreamer is to:

- CR+2 and CQ+2 the commits uploaded by CopyBot.
   * This step may be skipped if copybot is configured to auto-+2 for your repo.
   * Set auto +2 by adding `Verified+1, Bot-Commit+1, Commit-Queue+2` to the label
      field in your .ini file

- Watch for CQ failures.

- Manually handle commits with merge conflicts (if required).  You'll
  know this happens because you'll get an email from the CI failure:
  click the link for the output, and the list of commits with merge
  conflicts will be at the bottom of the page.

- Move commits with external dependency (e.g., a Cq-Depend, or an API
  change that requires rework of other code) out of the CL stack and
  handle merging these manually, as required.  See
  [Managing Conflicted Commits](#managing-conflicted-commits) below.

### Conflicts

#### CopyBot Conflict Behavior

CopyBot supports the following conflict behaviors specified on the command
line with the `--merge-conflict-behavior` option:

- `FAIL`: Fail the copybot run immediately; do not upload any changes.
- `SKIP`: Skip the conflicted CL, continue cherry-picking, and upload the
  resulting CL stack.
- `STOP`: Stop at the first conflicted CL, and upload the CLs which were
  cherry-picked before encountering the conflict.  CopyBot will exit with
  failure status.
- `ALLOW_CONFLICT`: After failing to cherry-pick a CL cleanly, commit the CL
  with unresolved conflict markers (`<<<<<<<` / `=======` / `>>>>>>>`), add
  `Commit: false` to the commit message body to prevent accidental merges, and
  upload the CL to Gerrit as a Work-In-Progress change. The original commit
  author is preserved and notified on Gerrit so they can rebase and resolve the
  conflict directly without the change being dropped or silently forgotten.
  In multi-downstream or milestone waterfall runs, conflict errors are deferred
  until all downstream targets are attempted, ensuring that other target branches
  still receive cleanly applicable commits. CopyBot will exit with a failure
  status at the end of the run if any branch encountered conflicts.

#### Managing Conflicted Commits

On a rare occasion, it may be necessary to skip or modify certain
commits to manage conflicts or so they can be merged later.
[Skipping](#skipping-commits) and [preserving](#preserving-commits)
can be accomplished by applying the corresponding Gerrit hashtag to
a pending change.  You may need to click the "More" button on the
left panel to see and add the hashtags.  The only requirement is that you
include the full upstream commit hash somewhere in the commit message.

If you change your mind in the future about the conflicted behavior,
just remove the corresponding hashtag.

#### Skipping Commits

To skip a commit, apply the Gerrit hashtag `copybot-skip` to a pending
change in the Gerrit UI.

On CopyBot's next run, it will respect your wishes, and no longer
upload any commits which came from this upstream revision.

#### Ignore Listing Commits

It is sometimes necessary to ignore a change for the lifetime of a repository.
In an effort to minimize the number of pending changes left in the downstream
repository, an ignore list CL can be used to list all of the hashes which
should be ignored within a repository.  To accomplish this:
- Upload a CL to the corresponding downstream repo listing the commit ID(s) to
  be ignored in the commit message body.
  - If this particular Copybot job uses subtrees, it is necessary to include a
    placeholder file in the CL that is located at the same directory prefix
    where upstream commits would be applied. The name or content of this file is
    immaterial. Some examples repeat the list of skipped CLs in this file, but
    this is unnecessary.

    For an example, see [Ignore List Example with Subtrees]. In this
    example, the upstream picolibc commits are applied to the subtree directory
    `[zephyrproject]/modules/lib/picolibc`, so the placeholder file must be
    located in `modules/lib/picolibc` as well.

    A single ignore list CL can cover an entire downstream repository. However,
    each upstream repo with commits to be skipped must have its own placeholder
    file at its corresponding subtree directory prefix. The commit message of
    the CL can contain the combined skipped commit IDs across upstream repos.
- Add the downstream project hashtag and `copybot-skip` hashtag as you normally
  would from the instructions in [skipping commits](#skipping-commits).
- The CL should not be merged. It can either be left open or marked as
  abandoned.

On GoB, it is highly encouraged to add `Commit: false` to the commit message
to prevent the CL from merging.

See [Ignore List Example].

#### Preserving Commits

To preserve a commit, apply the Gerrit hashtag `copybot-preserve` to
a pending change in the Gerrit UI.

On CopyBot's next run, it will cherry-pick the pending change from the
GoB instance associated with your downstream repo instead of overwriting
it with a change from the upstream repo.

#### Rebasing Commits

Sometimes it becomes necessary to modify a chain such as skipping or abandoning
a change.  In these instances it may be useful to rebase.  There isn't an easy
way to do this in the Gerrit UI or via the gerrit CLI, so to have copybot rebase
the stack, apply the Gerrit hashtag `copybot-rebase` to the first change you
wish to have rebased.  The subsequent copybot run will rebase the entire stack
on ToT starting from the commit with the hashtag.

> [!NOTE]
> After the next copybot run, the `copybot-rebase` hashtag should be removed to
> avoid additional rebases.

#### Abandoning Commits

Copybot will also consider abandoned commits as a part of the history of a repo.
Should you choose to always ignore a change that has already been uploaded, you
can leave the hashtags on the change, and abandon it.

### Triggering CopyBot Manually

You can trigger CopyBot jobs from the [LUCI Scheduler UI].

### Adding a CopyBot Configuration

CopyBot jobs are run and managed by LUCI.  To add or modify a job configuration,
modify the corresponding .ini configuration file found in infra/copybot/config.
Project specific configurations belong in `group_config.ini` and define the
scheduling interval, notification e-mail list, and jobs which should be manually
triggered.

Generate and/or update the .ini file by running copybot locally with the
--generate-config command line option.  See the help for more info.

Once the config file is uploaded, copybot will auto-generate LUCI builder configs
and launch the new job (see [copybot.star] and the [Config Manager Job]).

### CopyBot support for repositories

CopyBot supports local and external Git repositories.

#### Subtree paths

Copybot supports the selective filtering of files by explicit inclusion,
exclusion, and subtree paths.  Any of these options can be applied to the
up or downstream branches and can be used to keep files or paths in sync
in repositories that are otherwise unrelated.

#### Processing limits

CopyBot supports limiting both the up and down stream histories when
evaluating which changes to cherry-pick.  This can be especially useful
when configuring repositories which have been previously manually synced.
Setting the upstream limit ignores CLs in the upstream repo beyond the
limit.

CopyBot will automatically increase the upstream limit to find the
historical relationship between two repositories.

CopyBot also supports a maximum limit of CLs to downstream in a single
stack.  This can be useful when using a CLI/UI to trigger review/commit
and downstream CI limits the number of changes which can be processed
simultaneously.

#### Preserving fields in the commit message

CopyBot by default will prepend `Original-` to all pseudoheaders
of the format `Key: Value` found in the original commit message.
To prevent the addition of the `Original-` string to the key, use
the --keep-pseudoheader command line argument to tell CopyBot which
pseudoheaders should be preserved.  Some examples of pseudoheaders
which are often preserved are:

- `Cq-Depend`
- `Change-Id`

`Change-Id` is preserved by default when both the upstream and downstream are
detected to be a GoB server.

#### Skipping CLs cherry-picked by another CopyBot job

CopyBot supports adding pseudoheadres through the `--add-pseudoheader`
command line option.  By default, a `Copybot-Job-Name` pseudoheader
is added to CLs cherry-picked by the LUCI copybot jobs.  If for example
a CopyBot job is configured in which the upstream is also a downstream
of another copybot job, the CLs cherry-picked by copybot to the upstream
repository can be skipped using the `--skip-job-name` pseudoheader
identifying the value in the `Copybot-Job-Name` pseudoheader of the other
CopyBot job.

#### Additional CL Behaviors

Some additional features have been requested by groups using CopyBot and
may be of use:

- `--add-signed-off-by`: Sign-off the commit message.  This entirely defeats
  the purpose of `Signed-off-by` as robots cannot legally sign anything, but
  you do you.
- `--prepend-subject`: Prepend a string to the the commit message subject.
- `--push-option`: Can be passed multiple times, updates push refspec.
- Maintaining the `Change-Id` to show CL relationships.
  - This is the default for GoB and is reflected in the Gerrit UI.
- Adding Hashtags.
- Adding labels.
- Skipping, rebasing, and preserving pending changes.
- Adding reviewers.
- Adding CC.

### First-Parent History Traversal (`--first-parent`)

By default (`--first-parent = True`), CopyBot restricts git log traversals to
first-parent history when searching for commits to copy. This prevents CopyBot from
traversing into merged side branches or external pull request histories that might
contain commits already integrated through merge commits. Pass `--no-first-parent`
if full graph traversal is required.

### Multi-Downstream Support (`--downstreams`)

In addition to single `downstream-url` configurations, CopyBot supports
multi-downstream configs where a single upstream feeds multiple downstreams defined
as a dictionary in `.ini` files:

```ini
[copybot]
upstream-url = "https://example.googlesource.com/repo:main:"
downstreams = {
    "target-a": {
        "downstream-url": "https://example.googlesource.com/repo-a:main:",
        "history-starts-with": "abcd1234...",
    },
    "target-b": {
        "downstream-url": "https://example.googlesource.com/repo-b:main:",
        "history-starts-with": "ef012345...",
    }
}
```

#### Testing copybot changes locally

To test how a copybot change would have handled a problematic commit in the past
you can run with a local dir as the downstream, and reset to the commit before
the one you want to test.

For example, I want to test how copybot will handle commit
`508ea4e12a3ca06b50f3feff0edab1c59a745856` (from upstream) in the pigweed repo,
so I find the downstream parent to that commit, and create a `main` branch
locally at that parent, in this case `1080d6e8`, then setup the local dir and
run copybot starting at my problematic commit, and stopping after 5 commits.

```shell
cd ~/chromiumos/src/third_party/pigweed
git branch -d main
git checkout -b main 1080d6e8
git remote remove downstream
git remote remove upstream
~/chromiumos/infra/copybot/copybot.py --dry-run \
  -c ~/chromiumos/infra/copybot/config/pigweed-main-copybot-downstream.ini \
  --upstream-history-starts-with 508ea4e12a3ca06b50f3feff0edab1c59a745856 \
  --downstream-url=$HOME/chromiumos/src/third_party/pigweed --limit=5 |& less
git log --name-only main..HEAD
```

If you then want to see more details on the created commits, use `git show
<hash>`.

### Establishing historical relationships with CopyBot

Historical relationships can be established with the `upstream-history-starts-with` and
`downstream-history-starts-with` command line/config file arguments.  Using these arguments will set the
revision hash to use for the target repo as the starting point for consideration for
downstreaming.  If a relationship can not be determined, and these arguments are set, copybot
will start at these points, assuming the author submitted a config which is valid, and start
cherry-picking forward. This option can also be used to limit the number of CLs in the history to look
at after history has been previously established.  This aids in reducing copybot runtime.

The original method alluded to in [Copybot's Design](#copybot_s-design), can still be used
to set a downstream CL to reference the commit hash of an upstream CL.  This can be
accomplished by adding the hash to the commit message(preferably
using the `GitOrigin-RevId` pseudoheader) and running CopyBot subsequently.
CopyBot considers downstream pending changes as part of the repository history
and will overwrite the commit and commit message with the change as it would
cherry-pick it.  See [Preserving Commits](#preserving-commits) if this is not
the desired behavior.


## Branch and Tag Dispatch Modes

CopyBot supports several advanced dispatch modes for repositories where commits
need to be routed to dynamically discovered branches, cascaded across milestones,
or mapped across kernel versions.

### Dynamic `BRANCH=` Tag Dispatch (`--dispatch-branch-tags`)

Used in ChromeOS firmware (`coreboot` and `platform/ec`) to automatically cherry-pick
commits on `main` to targeted board firmware branches (e.g., `firmware-skyrim-15390.B`)
without maintaining static branch configurations (see [go/copybot-firmware-branch-dispatch]).

* **Commit Pseudoheaders**: CopyBot parses `BRANCH=`, `Original-BRANCH=`, and `Branches:`
  trailers on recent `main` commits (within `--upstream-history-limit`).
* **Branch Resolution**: Supports exact branch names (e.g. `BRANCH=firmware-skyrim-15390.B`)
  and board shorthands (e.g. `BRANCH=skyrim` or `BRANCH=firmware-corsola`), matching against
  active remote branches via on-demand `git ls-remote --heads`.
* **History Bounding**: Uses `git merge-base(target_branch, main)` as `history_starts_with`
  for each resolved branch, so only commits newer than the branch split are evaluated.
* **Header Preservation**: Downstreaming configs (e.g. `config/coreboot/main.ini`)
  should include `--keep-pseudoheader BRANCH` and `--keep-pseudoheader Change-Id` so
  upstream `review.coreboot.org` tags survive downstreaming into ChromeOS `main`.

Example configuration (`config/coreboot/branch-dispatch.ini`):
```ini
[copybot]
topic = "coreboot-branch-dispatch"
upstream-url = "https://chromium.googlesource.com/chromiumos/third_party/coreboot:main:"
downstream-url = "https://chromium.googlesource.com/chromiumos/third_party/coreboot:main:"
dispatch-branch-tags = True
upstream-history-limit = 100
merge-conflict-behavior = ALLOW_CONFLICT
keep-pseudoheader = ["Cq-Depend", "BUG", "TEST", "BRANCH", "Change-Id"]
```

### Dynamic Milestone Waterfall Dispatch (`--dynamic-downstream-pattern`)

Solves the missing intermediate milestone backport problem (see [b/570505987](https://b.corp.google.com/issues/570505987)),
where fixes backported to an older milestone branch (`firmware-R150-*`) are overlooked
on intermediate branches (`R151`, `R152`, etc.) before reaching `main`.

* **Dynamic Discovery**: Scans remote heads via `git ls-remote` using a regex pattern
  (e.g. `--dynamic-downstream-pattern "^firmware-R(\d+)-[\d.]+\.B(?:-main)?$"`).
  The milestone number is extracted via the capture group `(\d+)` (or an optional
  named group `(?P<milestone>\d+)`).
* **Milestone Filtering & Ordering**: Filters branches by `--min-milestone <M>` (e.g. `150`),
  sorts them numerically, and appends `main` if `--include-main-branch` is set.
* **Waterfall Cascade**: For each milestone branch `R_i`, CopyBot identifies candidate
  commits in the post-branch window (`merge-base(R_i, main)..R_i`) and cascades them to
  every newer milestone branch `R_j` (`j > i`) and `main`.
* **Deduplication**: Automatically skips commits that already exist on downstream branches
  or have pending/abandoned Gerrit CLs matching the commit's `Change-Id` or origin revision.
* **Conflict Resiliency**: When paired with `--merge-conflict-behavior ALLOW_CONFLICT`,
  conflicting commits are uploaded as WIP Gerrit changes, and conflict errors are deferred
  until all milestone branches have been processed.

Example configuration (`config/ec/milestone-dispatch.ini`):
```ini
[copybot]
topic = "ec-milestone-dispatch"
upstream-url = "https://chromium.googlesource.com/chromiumos/platform/ec:main:"
downstream-url = "https://chromium.googlesource.com/chromiumos/platform/ec:main:"
dynamic-downstream-pattern = "^firmware-R(\d+)-[\d.]+\.B-main$"
min-milestone = 150
include-main-branch = True
merge-conflict-behavior = ALLOW_CONFLICT
label = ["Commit-Queue+1"]
```

### Kernel CL Dispatcher (`--enable-kernel-cl-dispatcher`)

Automates dispatching bug fixes across ChromeOS kernel versions (see [go/kernel-cl-dispatch]).

* **Trailers**: Parses `Fixes:` and `Branches:` trailers on commits landed in the ChromeOS kernel.
* **History Bounding**: Uses `--downstream-cl-dispatcher-history-starts-with` to mark the
  historical SHA boundary when checking whether the fixed commit exists on the downstream target.
* **Group Mapping**: Branch groups defined in `group_config.ini` map logical group names to
  concrete downstream kernel branches.

## CopyBot Status

To see the status of an individual CopyBot job, go to the corresponding LUCI
builder in the [LUCI Scheduler UI].  For a more composite view, visit the
[CopyBot Status Dashboard].  Jobs are named with the config file name/hierarchy.

## Contributing to CopyBot

First of all, thanks!  Your improvements are very much welcomed!

Python source code must be formatted and verified using standard ChromeOS tools:
* **Format code:** `cros format .` and `black .`
* **Run tests & linters:** `./run_tests.sh`

`./run_tests.sh` executes the full verification suite:
1. `pytest` unit & integration tests (`./pytest_wrapper -v`)
2. Static type checking (`mypy .`)
3. Code linting (`cros lint . --exclude OWNERS`)
4. Formatting checks (`cros format --check .` and `black --check .`)

### Support

To file a bug against copybot please use [go/copybot-bug].

To ask questions or get help, please reachout to [copybot-maintainers@google.com]

### Tutorial Video

How to run it manually video: [copybot - manual run demo][copybot_video]
(available only to Googlers for the moment).

[Config Manager Job]: https://luci-scheduler.appspot.com/jobs/chromeos/config_manager-config-job-copybot-downstream
[Copybara]: https://github.com/google/copybara
[CopyBot Status Dashboard]: https://dashboards.corp.google.com/_e1e96010_77ed_4e80_8437_f8fb0bb0f77b
[copybot-maintainers@google.com]: mailto:copybot-maintainers@google.com
[copybot.star]: https://chrome-internal.googlesource.com/chromeos/infra/config/+/main/misc_builders/copybot.star
[copybot_video]: https://drive.google.com/file/d/10aG8cMnR6TOpY5veLWSXX4MGeHA0RGel/view?resourcekey=0-zeid4OuuFAsysx0dJomQWg
[go/copybot-bug]: http://go/copybot-bug
[go/copybot-firmware-branch-dispatch]: https://goto.google.com/copybot-firmware-branch-dispatch
[go/kernel-cl-dispatch]: https://goto.google.com/kernel-cl-dispatch
[Ignore List Example]: https://chromium-review.googlesource.com/c/chromiumos/third_party/zephyr/nanopb/+/5349888
[Ignore List Example with Subtrees]: https://chromium-review.git.corp.google.com/c/chromiumos/third_party/zephyrproject/+/8234379
[LUCI Scheduler UI]: https://luci-scheduler.appspot.com/jobs/chromeos
