# CopyBot

CopyBot is a tool that automates commit copying from a third-party
repository into Gerrit.  Currently, it's used by the Zephyr and
Coreboot projects.

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

`copybot.py`, `gerrit.py`, and `copybot_argparser.py`

`copybot.py` is the main entry point and is what to run to copy code from one
repository to another.  Its general usage is:

```
./copybot.py [options...] <upstream_repo>:<upstream_branch>:<upstream_subtree> <downstream_repo>:<downstream_branch>:<downstream_subtree>
```

Run `./copybot.py --help` for a complete list of options supported.

`copybot.py` will then:

1. Search for the first commit in the downstream repo which has
   `GitOrigin-RevId` or `Original-Commit-Id` specified in the footers,
   or has the exact same hash as as an upstream commit.

2. Identify the commits that need cherry-picked from that commit up to
   the upstream limit (keeping in mind file filtering patterns may
   cause certain commits to be skipped).

3. Cherry-pick those commits onto the downstream repo.

4. Push the changes to Gerrit.

Use `--dry-run` to test changes without pushing to the downstream repository.

`gerrit.py` aims to encapsulate the functionality needed by copybot to interact
with git and GoB repos.  Common functions for managing queries, changes, metadata,
refspecs, and pushing copied stacks can be found here.

`copybot_argparser.py` aims to minimize the content in the main script by
encapsulating the parsing, checking, and application of command line arguments.

## Using CopyBot

CopyBot is intended to be run daily as a cron job.  The Chromium OS
deployment of CopyBot is scheduled to run at 6 hour intervals starting at
~4:30 AM Mountain Time, but is configurable on a per project basis in `copybot.star`

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
line with the `--merge-conflict-behavior` option

- `FAIL`: Fail the copybot run immediately; do not upload any changes.
- `SKIP`: Skip the conflicted CL, continue cherry-picking, and upload the
  resulting CL stack.
- `STOP`: Stop at the first conflicted CL, and upload the CLs which were
  cherry-picked before encountering the conflict.  Copybot will exit with
  failure status.
- `ALLOW_CONFLICT`: After failing to cherry-pick a CL, commit the CL with
  unresolved conflicts.  Copybot will exit with failure status.

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

#### Preserving Commits

To preserve a commit, apply the Gerrit hashtag `copybot-preserve` to
a pending change in the Gerrit UI.

On CopyBot's next run, it will cherry-pick the pending change from the
GoB instance associated with your downstream repo instead of overwriting
it with a change from the upstream repo.

#### Ignore Listing Commits

It is sometimes necessary to ignore a change for the lifetime of a repository.
In an effort to minimize the number of pending changes left in the downstream
repository, an ignore list CL can be used to list all of the hashes which
should be ignored within a repository.  To accomplish this:
- upload a CL to the corresponding downstream repo(if subtrees are used, it
must also be within the desired subtree) with the list of commit hashes to be
skipped in the CL.
- Add the downstream topic and `Copybot-Skip` hashtag as you normally would from
  the instructions in [skipping commits](#skipping-commits).

On GoB, it is highly encouraged to add `Commit: false` to the commit message
to prevent the CL from merging.

See [Ignore List Example].

#### Abandoning Commits

Copybot will also consider abandoned commits as a part of the history of a repo.
Should you choose to always ignore a change that has already been uploaded, you
can leave the hashtags on the change, and abandon it.

### Triggering CopyBot Manually

You can trigger CopyBot jobs from the [LUCI Scheduler UI].

### Adding a CopyBot Configuration

CopyBot jobs are run and managed by LUCI.  To add or modify a job configuration,
modify the corresponding configuration object in
[`infra/config/misc_builders/copybot.star`].  This configuration is paired
with the .ini configuration file found in infra/copybot/config and the name
used as the config file name must match the builder name.

Generate and/or update the .ini file by running copybot locally with the
--generate-config command line option.  See the help for more info.

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

As alluded to in [Copybot's Design](#copybot_s-design), a downstream
CL must reference the commit hash of an upstream CL.  This can be
accomplished by adding the hash to the commit message(preferably
using the `GitOrigin-RevId` pseudoheader) and running CopyBot subsequently.
CopyBot considers downstream pending changes as part of the repository history
and will overwrite the commit and commit message with the change as it would
cherry-pick it.  See [Preserving Commits](#preserving-commits) if this is not
the desired behavior.

Historical relationships can also be established with the `upstream-history-starts-with` and
`downstream-history-starts-with` command line arguments.  Using these arguments will set the
revision hash to use for the target repo as the starting point for consideration for
downstreaming.  This option can also be used to limit the number of CLs in the history to look
at after history has been previously established.  This aids in reducing copybot runtime.

## CopyBot Status

To see the status of an individual CopyBot job, go to the corresponding LUCI
builder in the [LUCI Scheduler UI].  For a more composite view, visit the
[CopyBot Status Dashboard].

## Contributing to CopyBot

First of all, thanks!  Your improvements are very much welcomed!

Python source code should be auto-formatted by `black` and `isort`.
Run `black .` and `isort .` to do the formatting.

To run tests, use `./run_tests.sh`.

### Support

To file a bug against copybot please use [go/copybot-bug].

To ask questions or get help, please reachout to [copybot-maintainers@google.com]

### Tutorial Video

How to run it manually video: [copybot - manual run demo][copybot_video]
(available only to Googlers for the moment).

[Copybara]: https://github.com/google/copybara
[CopyBot Status Dashboard]: https://dashboards.corp.google.com/_e1e96010_77ed_4e80_8437_f8fb0bb0f77b
[copybot-maintainers@google.com]: mailto:copybot-maintainers@google.com
[copybot_video]: https://drive.google.com/file/d/10aG8cMnR6TOpY5veLWSXX4MGeHA0RGel/view?resourcekey=0-zeid4OuuFAsysx0dJomQWg
[go/copybot-bug]: http://go/copybot-bug
[Ignore List Example]: https://chromium-review.googlesource.com/c/chromiumos/third_party/zephyr/nanopb/+/5349888
[`infra/config/misc_builders/copybot.star`](https://chrome-internal.googlesource.com/chromeos/infra/config/+/main/misc_builders/copybot.star).
[LUCI Scheduler UI]: https://luci-scheduler.appspot.com/jobs/chromeos
