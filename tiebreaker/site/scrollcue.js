/* Drop the edge fade on any horizontally scrollable box once you have
   reached its end. Same behaviour as the attendance tracker's season grid —
   a cue that never goes away stops meaning anything. */
(function () {
  var boxes = document.querySelectorAll(".scrollbox");
  if (!boxes.length) return;
  boxes.forEach(function (box) {
    var update = function () {
      var max = box.scrollWidth - box.clientWidth;
      box.classList.toggle("at-end", max <= 2 || box.scrollLeft >= max - 2);
    };
    box.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    if (window.ResizeObserver) new ResizeObserver(update).observe(box);
    update();
  });
})();
