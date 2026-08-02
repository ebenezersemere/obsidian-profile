<%*
if (tp.file.title === "template") { throw new Error("safeguard: refusing to apply quarterly template to template.md itself"); }
const q = moment(tp.file.title, "[Q]Q, YYYY", true);
if (!q.isValid()) { throw new Error(`quarterly template: title "${tp.file.title}" does not match "[Q]Q, YYYY"`); }
await tp.file.move(`time/quarterly/${tp.file.title}`);
const prev = q.clone().subtract(1, "quarter").format("[Q]Q, YYYY");
const next = q.clone().add(1, "quarter").format("[Q]Q, YYYY");
const year = `${q.year()}`;
-%>
> [!navigation]+ navigation
> [[<% prev %>|← last quarter]] · [[<% next %>|next quarter →]] · [[<% year %>]]
