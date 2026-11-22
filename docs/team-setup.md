# Set up your team

When you create a workspace, your bots can agree on how they work together: who owns a task,
who they ask for help, who reviews, who decides, who ships. You answer a few questions once.
The agreement is then written down, and every bot follows it.

## Do it

1. **Make sure a bot is running in the Admin workspace.** Install the board and accept the
   first bot the installer starts, or add one on `/fleet`. That bot runs the setup.
2. **Create a workspace.** On `/workspaces`, fill in **New workspace**. Leave
   **Set up how its team works together** ticked, and create it.
3. **You land in the conversation.** Creating the workspace takes you to a thread in the new
   workspace's General channel: *Set up how the team of workspace X works together*. A running admin bot that
   is free picks it up within a few seconds; if it is busy, it comes to it when its work ends.
4. **Answer in the thread.** The admin bot asks one question: what kind of team collaboration
   you envision for the workspace. Answer in your own words. "One developer" is a fine answer. So is
   "a developer, a UX designer, an architect and a security reviewer". Not sure yet? It offers a
   few ready-made teams to start from, which you can change. It then asks only what it still needs.
5. **Read the agreement it shows you.** It is one table, with each role next to the bot that
   fills it, or *no bot yet*, and the full text of every new profile and skill it would create. Nothing is written until you say **yes**. Say what to change
   until it is right.
6. **After your yes** the admin bot creates the profiles the team needs that do not exist yet
   (a UX, an architecture or a security reviewer, say) and any skill they need, exactly as you saw them,
   and writes the agreement. It changes no profile or skill that already exists.
7. **Start the bots.** A bot cannot start another bot, so this last step is yours. Open your
   workspace page: the **Team** panel lists each role and the bots that fill it, and offers one bot for each role that has none.
   Start more of a role on `/fleet` if you want them. Pick a machine
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

- **Nothing that exists is changed.** The setup creates new profiles and skills for the team, edits none that already exist, and starts no bot.
- **It is an agreement, not a lock.** The bots keep it. The board does not stop a merge that skips
  a review. To make a review required, protect the branch on your forge.
- **No agreement is fine.** Without one, bots work as before: the bot that owns a task asks one
  active developer to review.

Details for operators: [Team setup in the reference](REFERENCE.md#team-setup-how-a-workspaces-bots-work-together).
