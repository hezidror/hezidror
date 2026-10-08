document.getElementById('year').textContent = new Date().getFullYear();

const menuToggle = document.getElementById('menuToggle');
const navLinks = document.querySelector('.nav-links');

menuToggle.addEventListener('click', () => {
  navLinks.classList.toggle('open');
});

navLinks.querySelectorAll('a').forEach((link) => {
  link.addEventListener('click', () => navLinks.classList.remove('open'));
});

// Highlight today's opening hours and show whether the salon is open now
const hoursList = document.getElementById('hoursList');
const openStatus = document.getElementById('openStatus');

if (hoursList && openStatus) {
  const now = new Date();
  const today = hoursList.querySelector(`[data-day="${now.getDay()}"]`);

  if (today) {
    today.classList.add('today');
    const match = today.lastElementChild.textContent.match(/(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})/);
    const minutes = now.getHours() * 60 + now.getMinutes();
    const isOpen = match &&
      minutes >= match[1] * 60 + +match[2] &&
      minutes < match[3] * 60 + +match[4];

    openStatus.textContent = isOpen ? 'פתוח עכשיו' : 'סגור כרגע';
    openStatus.classList.add(isOpen ? 'is-open' : 'is-closed');
    openStatus.hidden = false;
  }
}

// Fade sections in as they scroll into view
const revealEls = document.querySelectorAll('.reveal');

if ('IntersectionObserver' in window) {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12 });

  revealEls.forEach((el) => observer.observe(el));
} else {
  revealEls.forEach((el) => el.classList.add('visible'));
}
