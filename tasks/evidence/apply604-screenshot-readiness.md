# Apply screenshot readiness

The first composed gate at `73ee000f` exposed two evidence races. The A16 held desktop light
image captured an unloaded application image and a truncated held card. Both checkpoint desktop
images captured `Loading your queue`, and checkpoint mobile light captured collapsed images.
Checkpoint mobile dark showed the loaded queue. These were inspected from the retained synthetic
gate artifacts; they do not establish a product failure.

`makeShots` now requests rendered lazy images even outside the viewport, waits for image decode
and font readiness, and requires consecutive equal document and image geometry measurements
before checking overflow and taking each screenshot. Image decode errors propagate. No fixed sleep,
application behavior, test timeout or service budget is added or changed.

A16 waits for the named held card and its own timer before held evidence. Checkpoint evidence
waits for both named queue cards: posting 4100000109 must show the review status, the specific
LinkedIn security check return reason and the Review the answers button; posting 4100000110 must
show that the form needs the person and nothing was sent. These assertions belong to the callers,
so intentional loading state evidence elsewhere stays available.

A17 captures the owner's settled review with the saved synthetic marker and enabled approval,
then the other person's settled Page not found view of the private posting, each at both widths
and colour schemes. The other person's body must omit the marker before and after capture. The
signed-out and foreign-user API refusal, no-store, marker and exact one approved fake submission
assertions stay in place.

`apply-shot-readiness.spec.ts` adds a controlled delayed offscreen lazy image and a broken image
case. The first holds its local intercepted response until readiness requests it and checks that
no capture started early, then inspects all four captures. The second requires decode failure.
Runtime execution, mutation proof and type checking are deferred while the first full gate owns
the machine. The next composed gate must rerun A16 and regenerate its actual evidence.
