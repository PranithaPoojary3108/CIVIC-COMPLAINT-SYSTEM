// Auto-dismiss flash messages after 5s
document.querySelectorAll('.flash').forEach(el => {
  setTimeout(() => el.style.opacity = '0', 5000);
  setTimeout(() => el.remove(), 5500);
  el.style.transition = 'opacity .5s';
});
