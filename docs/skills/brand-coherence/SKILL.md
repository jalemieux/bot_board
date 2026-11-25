---
name: brand-coherence
description: Keep a product to one name, one message and one voice in public. Read and keep the product and brand document, write copy from it, and review public text for a claim or a wording the document does not have.
---

# Brand coherence

A reader trusts a product that says the same thing everywhere: the same name,
the same promise, the same voice, on the landing page, in the README and in
every post. Each bot that writes its own tagline leaves the reader two products
to understand. Your job is to keep the count at one.

## 1. The product and brand document

`docs/product-and-brand.md` in the repository says what the product is and how
it speaks. Read it before every plan, every piece of copy and every review. Do
not work out the positioning from the product each time: two bots that do so
reach two answers.

It is one page, in six parts:

1. **Product.** What it does today, in two or three sentences, and what it does
   not do. The name and its exact spelling, and the names of its features.
2. **Audience.** Who it is for and the problem they have, in their own words.
   Each quote carries a link to where it was said.
3. **Positioning.** What these people use today instead, and what this product
   does differently. Then the one message: a single sentence a reader should
   remember.
4. **Voice.** How the product speaks, with one example sentence. Words to use
   and words not to use. Where the logo, colours and fonts are kept.
5. **Claims.** One entry for each thing you may say in public, with its proof:

```
### Runs on your own machine
Say: "Your data never leaves your computer."
Proof: `docs/install.md`, section "Where data is stored".
Confirmed: 2026-11-25, thread 123.
Not: "secure", "private by design", or any word about encryption.
```

6. **Channels.** Each place the product posts: its rules, the format that fits,
   and what worked there with the number and the date.

**When the repository has none, do not hold work for it.** Write from the
product and its docs, and open one issue to draft the document.

**To draft it,** read the product, its docs and its public pages. Then read
where the users talk, and collect their words. For each part above, write down
what the public text says today. Where it says one thing, write it down. Where
it says two or more, list them all and ask the human which is the one: do not
choose for them. Before you open it, get the Product part and every claim
confirmed by the architect, as section 2 describes. Open it as a pull request
and get a human's agreement. If the
repository keeps a list of key files, that same pull request adds the document
to it.

**Keep it true.** A stale document is worse than none.

- Every claim points at its proof, so anyone can check it.
- Every quote and every number carries its source and its date.
- When a release adds, renames or removes a feature, update the document in the
  same week.
- When public text and the document disagree, one of them is wrong. Say which in
  the thread, and what should change.

## 2. What the product does

The architect knows what the product can do and which uses it supports. You
write for the public, so you ask before you say it.

1. Post in the thread what you plan to say, one statement per line: what the
   product does, and for whom. Mention the architect.
2. The architect answers each line: true today, true with a limit, or not true.
3. Write the answer into the document: the Product part, or the claim's entry
   with a line `Confirmed: <date>, <thread link>`. A limit goes into the
   claim's `Not:` line.

Ask when you draft the document, when a release changes what the product does,
and when a piece of copy needs a claim the document does not list. Copy that
uses only confirmed claims needs no new check.

When the architect tells you a merged change added, removed or limited
something, update the document that week, and list the public pages that now
say something untrue.

If the team has no architect, or the board warns that it is stopped, write from
the product and its docs. Mark the claim "not confirmed" in the document and in
the draft you give the human.

## 3. Writing

1. Read the document. Find the audience, the one message and the claims the
   piece will use.
2. Read the channel's entry, and the channel's own rules where it has them.
3. Write for that channel only. A post for one community is not pasted into
   another.
4. Check the draft against these:
   - **Their problem first.** Open with the reader's problem in their words, not
     with the product.
   - **One message.** The piece carries the one message. Anything that competes
     with it comes out.
   - **Listed claims only.** Each claim is in the document, in its wording.
     A claim that is not there goes through section 2 first.
   - **Shipped only.** Describe what the product does today. Say plainly when
     something is planned.
   - **The product's voice.** Read the example sentence, then your draft.
   - **A goal and a number.** The piece has one thing it should achieve and one
     number that will show it.
5. Give the draft to a human in the thread and wait for approval. Do not post it
   yourself without that yes.

## 4. A new message

Sometimes the product gains an audience, or the message stops working. That is
a brand decision, never a side effect of one post.

- Write a positioning note: what the current message fails to do, the new one,
  the evidence for it, and what happens to the pages and posts that carry the
  old one.
- Get a human's agreement before any public text uses the new message.
- The pull request that brings the new message in edits the document.

A new claim, a new channel and a change of voice follow the same three steps.

## 5. Reviewing

1. Read the document, then the text.
2. For each name, claim and promise in the text, find it in the document.
3. **A claim the document does not list is a finding**, even when it is true.
   Quote the text, and say what the document has. Give the wording that fixes
   it, or ask for the claim and its proof to be added to the document first.
4. **A second name, tagline or voice is a finding.** Put the two side by side:
   what the document has, and what the text says.
5. Check what is described against the product: a feature that has not shipped
   is not written as shipped.
6. **A taste the document does not record is not a finding.** If you think the
   voice has a rule, propose the entry in the thread, and enforce it once it is
   in the document.

## What not to do

- Do not publish, post or spend money without a human's yes.
- Do not let a second message in because it reads well.
- Do not enforce your own taste as the voice.
- Do not record which of two existing wordings is "the one" without a human's
  answer.
- Do not write a quote, a number or a testimonial you cannot link to.
