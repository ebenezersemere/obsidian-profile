<%*
if (tp.file.title === "template") { throw new Error("safeguard: refusing to apply yearly template to template.md itself"); }
const yr = moment(tp.file.title, "YYYY", true);
if (!yr.isValid()) { throw new Error(`yearly template: title "${tp.file.title}" does not match "YYYY"`); }
await tp.file.move(`time/yearly/${tp.file.title}`);
const prev = yr.clone().subtract(1, "year").format("YYYY");
const next = yr.clone().add(1, "year").format("YYYY");
-%>
> [!navigation]+ navigation
> [[<% prev %>|← last year]] · [[<% next %>|next year →]]
