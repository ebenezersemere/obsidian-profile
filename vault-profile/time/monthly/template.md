<%*
if (tp.file.title === "template") { throw new Error("safeguard: refusing to apply monthly template to template.md itself"); }
const mo = moment(tp.file.title, "MMMM, YYYY", true);
if (!mo.isValid()) { throw new Error(`monthly template: title "${tp.file.title}" does not match "MMMM, YYYY"`); }
await tp.file.move(`time/monthly/${tp.file.title}`);
const prev = mo.clone().subtract(1, "month").format("MMMM, YYYY");
const next = mo.clone().add(1, "month").format("MMMM, YYYY");
const year = `${mo.year()}`;
-%>
> [!navigation]+ navigation
> [[<% prev %>|← last month]] · [[<% next %>|next month →]] · [[<% year %>]]
