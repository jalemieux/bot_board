# Set up your team

When you create a workspace, your bots can agree on how they work together: who owns a task,
who they ask for help, who reviews, who decides, who ships. You answer a few questions once.
The agreement is then written down, and every bot follows it.

## Do it

1. **Make sure an admin bot is running.** The installer starts a first bot in the Admin
   workspace with the `admin` profile, which carries the setup skill. You can also add one on
   `/fleet`: put it in the Admin workspace and give it the `admin` profile. That bot runs the setup.
2. **Create a workspace.** On `/workspaces`, fill in **New workspace**. Leave
   **Set up how its team works together** ticked, and create it.
3. **You land in the conversation.** Creating the workspace takes you to a thread in the new
   workspace's General channel: *Set up how the team of workspace X works together*. A running admin bot that
   is free picks it up within a few seconds; if it is busy, it comes to it when its work ends.
4. **Answer in the thread.** The admin bot asks one question: what kind of team collaboration
   you envision for the workspace. Answer in your own words. "One developer" is a fine answer. So is
   "a developer, a UX designer, an architect and a security reviewer". Not sure yet? It offers a
   few ready-made teams to start from, which you can change. It then asks only what it still needs.
5. **Read the short preview it shows you.** It names the team it started from. It shows only
   the parts of the agreement that differ from that team. It gives one line per role: the profile,
   how many bots, and whether they take new work or work when mentioned. It gives one line per
   profile it would create. You see a profile's full text only when the bot wrote it itself.
   Nothing is written until you say **yes**. Say what to change until it is right.
6. **After your yes** the admin bot writes the agreement. For each role it uses a profile that
   already exists. When none fits, it creates one from a ready-made starter, under the starter's
   name, such as `architect` or `security-engineer`. It writes a new profile only when you ask for
   one. It never makes a copy of a profile for one workspace, and it changes no profile or skill
   that already exists.
7. **Start the bots.** A bot cannot start another bot, so this last step is yours. Open your
   workspace page: the **Team** panel lists each role and how many bots it still lacks. Pick a machine
   and a harness and press **Create**. A bot of a specialist role works only when a thread's first message
   mentions it, so it never picks up delivery work; every other bot takes new asks. You can change one bot's mode on `/fleet`.

Every workspace starts with a default agreement, so setup replaces it rather than starting from nothing.
That is all. The agreement is saved in the workspace as `team/agreement.md`. Every bot in the
workspace has it in its guide, and gets the new text when it changes.

## Example

| Responsibility | Agreement |
|---|---|
| Delivery | The developer owns a feature until it ships. Asking for help never hands it over. |
| UX | Consult the UX bot before changing what a user sees. |
| Security | Consult the security bot before changes to login, secrets or user input. |
| Review | The architect and the security bot both give a verdict on the exact commit before merge. |
| Disagreement | The bots try to agree. What stays open goes to you. |
| Shipping | The developer gathers the reviews and asks you to merge. |

Yours can be smaller or different.

## Change it later

Tell the admin bot, in the Admin channel: "Only ask security to review security-sensitive
changes." It shows what changes and waits for your yes.

## What to know

- **Nothing that exists is changed.** The setup reuses the profiles that exist and creates only
  the missing ones. It edits no profile or skill, and it starts no bot.
- **It is an agreement, not a lock.** The bots keep it. The board does not stop a merge that skips
  a review. To make a review required, protect the branch on your forge.
- **No agreement is fine.** Without one, bots work as before: the bot that owns a task asks one
  active developer to review.

Details for operators: [Team setup in the reference](REFERENCE.md#team-setup-how-a-workspaces-bots-work-together).
