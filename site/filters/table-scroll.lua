-- Wrap each table so a wide table scrolls inside its own region on narrow screens.
function Table(tbl)
  return pandoc.Div({ tbl }, pandoc.Attr("", { "table-scroll" }, { tabindex = "0", role = "region", ["aria-label"] = "Scrollable table" }))
end
