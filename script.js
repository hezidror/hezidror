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

// Gallery lightbox: click a photo to view it large, arrows/keys to browse
const lightbox = document.getElementById('lightbox');

if (lightbox) {
  const lightboxImg = lightbox.querySelector('img');
  let photos = [];
  let current = 0;
  let opener = null;

  const show = (index) => {
    current = (index + photos.length) % photos.length;
    lightboxImg.src = photos[current].src;
    lightboxImg.alt = photos[current].alt;
  };

  const close = () => {
    lightbox.hidden = true;
    document.body.style.overflow = '';
    if (opener) opener.focus();
  };

  // Browse within the gallery the clicked photo belongs to
  document.querySelectorAll('.gallery-item img').forEach((img) => {
    img.closest('.gallery-item').addEventListener('click', (e) => {
      opener = e.currentTarget;
      photos = Array.from(opener.closest('.gallery-grid').querySelectorAll('img'));
      show(photos.indexOf(img));
      lightbox.hidden = false;
      document.body.style.overflow = 'hidden';
      lightbox.querySelector('.lightbox-close').focus();
    });
  });

  lightbox.querySelector('.lightbox-close').addEventListener('click', close);
  lightbox.querySelector('.lightbox-prev').addEventListener('click', () => show(current - 1));
  lightbox.querySelector('.lightbox-next').addEventListener('click', () => show(current + 1));
  lightbox.addEventListener('click', (e) => { if (e.target === lightbox) close(); });

  document.addEventListener('keydown', (e) => {
    if (lightbox.hidden) return;
    if (e.key === 'Escape') close();
    if (e.key === 'ArrowRight') show(current - 1);
    if (e.key === 'ArrowLeft') show(current + 1);
  });
}

// Highlight the menu link of the section currently on screen
const navAnchors = document.querySelectorAll('.nav-links a[href^="#"]');
const sectionsByLink = Array.from(navAnchors)
  .map((link) => [link, document.querySelector(link.getAttribute('href'))])
  .filter(([, section]) => section);

const updateActiveLink = () => {
  const line = window.innerHeight * 0.35;
  let active = sectionsByLink[0];
  sectionsByLink.forEach((entry) => {
    if (entry[1].getBoundingClientRect().top <= line) active = entry;
  });
  navAnchors.forEach((link) => link.classList.toggle('active', link === active[0]));
};

if (sectionsByLink.length) {
  window.addEventListener('scroll', updateActiveLink, { passive: true });
  updateActiveLink();
}
