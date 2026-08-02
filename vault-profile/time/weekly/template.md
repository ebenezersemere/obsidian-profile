<%*
if (tp.file.title === "template") { throw new Error("safeguard: refusing to apply weekly template to template.md itself"); }
const wk = moment(tp.file.title, "[Week] ww, YYYY", true);
if (!wk.isValid()) { throw new Error(`weekly template: title "${tp.file.title}" does not match "[Week] ww, YYYY"`); }
await tp.file.move(`time/weekly/${tp.file.title}`);
const prev = wk.clone().subtract(1, "week").format("[Week] ww, YYYY");
const next = wk.clone().add(1, "week").format("[Week] ww, YYYY");
const year = `${wk.year()}`;
-%>
> [!navigation]+ navigation
> [[<% prev %>|← last week]] · [[<% next %>|next week →]] · [[<% year %>]]
