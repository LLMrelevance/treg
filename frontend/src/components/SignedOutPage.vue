<script>
import { useDashboard } from '../state/context'
export default { setup: useDashboard }
</script>

<template>
<div  class="lp">
    <!-- SHARE-LINK GATE: a /app/skills|tools/<name> deep link while logged out gets a focused
         sign-in page (no sandbox demo, no tour) — the marketing landing is for the front door. -->
    <template v-if="shareGate">
      <header class="lp-nav">
        <div class="brand">▚ treg</div>
        <nav class="lp-links"><a href="/tutorial">Docs</a><button class="iconbtn" @click="toggleTheme" :aria-label="theme==='dark'?'Light theme':'Dark theme'">{{theme==='dark'?'◐':'◑'}}</button></nav>
      </header>
      <section class="lp-hero" style="min-height:55vh">
        <div class="lp-eyebrow">shared with you</div>
        <h1 class="lp-h1">{{shareGate.name}} <span style="color:var(--accent)">· {{shareGate.kind}}</span></h1>
        <p class="lp-lead">A teammate shared this {{shareGate.kind}} with you. Sign in and you'll land right on its page — no API key ever touches your machine.</p>
        <div class="lp-cta"><button class="btn primary lg" @click="demo.signin=true">Sign in to view</button></div>
      </section>
    </template>
    <template v-else>
    <header class="lp-nav">
      <div class="brand">▚ treg</div>
      <nav class="lp-links">
        <a href="/tutorial">Docs</a><a href="/llms.txt">llms.txt</a>
        <button class="iconbtn" @click="toggleTheme" :aria-label="theme==='dark'?'Light theme':'Dark theme'">{{theme==='dark'?'◐':'◑'}}</button>
        <button class="btn sm" @click="demo.signin=true">Sign in</button>
      </nav>
    </header>

    <section class="lp-hero">
      <div class="lp-eyebrow">the tool catalog for your agent</div>
      <h1 class="lp-h1">Give your agent the tools for the job, <span style="color:var(--accent)">without buying the seats</span>.</h1>
      <p class="lp-lead">2,800+ endpoints across 59 providers — SEO and backlinks, social and trends,
        people and company enrichment, ads — <b>priced per call, from a cent</b>, with no provider signup.
        Plus your own team's keys and skills, callable by every teammate's agent, with the credential
        never leaving the server.</p>
      <div class="lc-chips"><span>one key, every provider</span><span>price shown before the call</span><span>your own key is never metered</span></div>
      <div class="lp-cta">
        <button class="btn primary lg" @click="demo.signin=true">Get started, free</button>
        <a class="btn lg" href="/tutorial">Read the docs →</a>
      </div>
    </section>


    <section class="lp-section">
      <div class="lp-head"><h2>Three ways a key leaks. treg closes all three.</h2>
        <p>The same secret, needed in more and more places - and every place is a copy that can leak.</p></div>
      <div class="lc-tri">
        <div class="lc-pain"><div class="t">Key sprawl</div><div class="p">The same key pasted into every project, <code>.env</code>, and machine. One leak exposes them all.</div><div class="f"><b>treg →</b> one place. The key is injected at call time and lives nowhere else.</div></div>
        <div class="lc-pain"><div class="t">Team access</div><div class="p">Give a teammate access and they hold your key - forever, everywhere they go.</div><div class="f"><b>treg →</b> they get a token, not the key. Revoke it and they're out.</div></div>
        <div class="lc-pain"><div class="t">Agent skills</div><div class="p">A skill needs credentials to run. Sharing the skill shouldn't mean shipping the secret.</div><div class="f"><b>treg →</b> the skill calls through treg. Your agent runs it and never sees the key.</div></div>
      </div>
    </section>

    <section class="lp-section" style="text-align:center">
      <div class="lp-head" style="margin:0 auto"><h2>Share your team's keys and skills, <span style="color:var(--accent)">without sharing your keys</span>.</h2>
        <p>Your team's tools and your agents' skills, all one revocable token away, and no key ever on a machine — alongside the 2,800+ catalog tools you don't need a key for at all.</p></div>
      <div class="lp-cta" style="margin-top:24px">
        <button class="btn primary lg" @click="demo.signin=true">Get started, free</button>
        <a class="btn lg" href="/tutorial">Read the docs →</a>
      </div>
    </section>

    <footer class="lp-foot">treg · a credential-injecting proxy · <a href="/tutorial">tutorial</a> · <a href="/llms.txt">llms.txt</a></footer>
    </template>

  </div>
</template>
