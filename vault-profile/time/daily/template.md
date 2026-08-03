<%*
if (tp.file.title === "template") { throw new Error("safeguard: refusing to apply daily template to template.md itself"); }
const day = moment(tp.file.title, "MMMM Do, YYYY", true);
if (!day.isValid()) { throw new Error(`daily template: title "${tp.file.title}" does not match "MMMM Do, YYYY"`); }
await tp.file.move(`time/daily/${tp.file.title}`);
const yesterday = day.clone().subtract(1, "day").format("MMMM Do, YYYY");
const tomorrow  = day.clone().add(1, "day").format("MMMM Do, YYYY");
const week    = day.format("[Week] ww, YYYY");
const month   = day.format("MMMM, YYYY");
const quarter = `Q${day.quarter()}, ${day.year()}`;
const year    = `${day.year()}`;
const iso     = day.format("YYYY-MM-DD");
-%>
---
date: <% iso %>
---

> [!navigation]+ navigation
> [[<% yesterday %>|← yesterday]] · [[<% tomorrow %>|tomorrow →]] · [[<% week %>|W<% day.format("ww") %>]] · [[<% month %>|<% day.format("MMM") %>]] · [[<% quarter %>|Q<% day.quarter() %>]] · [[<% year %>]]

> [!knowledge]+ knowledge
> ```dataview
> LIST WITHOUT ID file.link
> FROM "knowledge"
> WHERE file.mday = this.file.day
> SORT file.mtime DESC
> LIMIT 10
> ```
