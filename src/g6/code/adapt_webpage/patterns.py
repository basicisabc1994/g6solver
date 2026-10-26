# SPDX-License-Identifier: Apache-2.0

"""
patterns -- g6.code.adapt_webpage.patterns

Reusable HTML/CSS pattern templates and theme definitions for webpage generation.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Section patterns -- each has html (with placeholders) and css
# ---------------------------------------------------------------------------

_PATTERNS: dict[str, dict[str, str]] = {
    "hero-centered": {
        "html": (
            '<section class="hero-centered">\n'
            '  <div class="hero-content">\n'
            '    <h1>{title}</h1>\n'
            '    <p class="hero-subtitle">{description}</p>\n'
            '    <a href="#" class="hero-cta-btn">Get Started</a>\n'
            '  </div>\n'
            '</section>'
        ),
        "css": (
            '.hero-centered { display: flex; align-items: center; justify-content: center;'
            ' min-height: 80vh; text-align: center;'
            ' background: linear-gradient(135deg, var(--color-primary), var(--color-secondary)); color: #fff; }\n'
            '.hero-content { max-width: 700px; padding: 2rem; }\n'
            '.hero-centered h1 { font-size: 3rem; margin-bottom: 1rem; }\n'
            '.hero-subtitle { font-size: 1.25rem; opacity: 0.9; margin-bottom: 2rem; }\n'
            '.hero-cta-btn { display: inline-block; padding: 0.75rem 2rem; background: #fff;'
            ' color: var(--color-primary); border-radius: var(--border-radius);'
            ' text-decoration: none; font-weight: 600; }'
        ),
    },
    "hero-split": {
        "html": (
            '<section class="hero-split">\n'
            '  <div class="hero-split-text">\n'
            '    <h1>{title}</h1>\n'
            '    <p>{description}</p>\n'
            '    <a href="#" class="hero-cta-btn">Learn More</a>\n'
            '  </div>\n'
            '  <div class="hero-split-media">\n'
            '    <div class="hero-placeholder-img"></div>\n'
            '  </div>\n'
            '</section>'
        ),
        "css": (
            '.hero-split { display: grid; grid-template-columns: 1fr 1fr; min-height: 70vh;'
            ' align-items: center; gap: 2rem; padding: 2rem; }\n'
            '.hero-split-text { padding: 2rem; }\n'
            '.hero-split-text h1 { font-size: 2.5rem; margin-bottom: 1rem; }\n'
            '.hero-split-media { display: flex; align-items: center; justify-content: center; }\n'
            '.hero-placeholder-img { width: 100%; aspect-ratio: 4/3;'
            ' background: var(--color-accent); border-radius: var(--border-radius); }\n'
            '@media (max-width: 768px) { .hero-split { grid-template-columns: 1fr; } }'
        ),
    },
    "features-grid": {
        "html": (
            '<section class="features-grid">\n'
            '  <h2>{title}</h2>\n'
            '  <div class="features-cards">\n'
            '    <div class="feature-card"><h3>Feature 1</h3><p>Description of feature one.</p></div>\n'
            '    <div class="feature-card"><h3>Feature 2</h3><p>Description of feature two.</p></div>\n'
            '    <div class="feature-card"><h3>Feature 3</h3><p>Description of feature three.</p></div>\n'
            '  </div>\n'
            '</section>'
        ),
        "css": (
            '.features-grid { padding: 4rem 2rem; text-align: center; }\n'
            '.features-grid h2 { font-size: 2rem; margin-bottom: 2rem; }\n'
            '.features-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));'
            ' gap: 1.5rem; max-width: 1200px; margin: 0 auto; }\n'
            '.feature-card { padding: 2rem; background: var(--color-surface);'
            ' border-radius: var(--border-radius); box-shadow: var(--shadow); }\n'
            '.feature-card h3 { margin-bottom: 0.75rem; color: var(--color-primary); }'
        ),
    },
    "features-alternating": {
        "html": (
            '<section class="features-alt">\n'
            '  <div class="feature-row">\n'
            '    <div class="feature-text"><h3>Feature A</h3><p>Detailed description.</p></div>\n'
            '    <div class="feature-img"><div class="placeholder-img"></div></div>\n'
            '  </div>\n'
            '  <div class="feature-row reverse">\n'
            '    <div class="feature-text"><h3>Feature B</h3><p>Another detailed description.</p></div>\n'
            '    <div class="feature-img"><div class="placeholder-img"></div></div>\n'
            '  </div>\n'
            '</section>'
        ),
        "css": (
            '.features-alt { padding: 4rem 2rem; max-width: 1200px; margin: 0 auto; }\n'
            '.feature-row { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem;'
            ' align-items: center; margin-bottom: 3rem; }\n'
            '.feature-row.reverse { direction: rtl; }\n'
            '.feature-row.reverse > * { direction: ltr; }\n'
            '.placeholder-img { width: 100%; aspect-ratio: 16/9;'
            ' background: var(--color-accent); border-radius: var(--border-radius); }\n'
            '@media (max-width: 768px) { .feature-row { grid-template-columns: 1fr; }'
            ' .feature-row.reverse { direction: ltr; } }'
        ),
    },
    "pricing-cards": {
        "html": (
            '<section class="pricing">\n'
            '  <h2>Pricing</h2>\n'
            '  <div class="pricing-cards">\n'
            '    <div class="pricing-card">\n'
            '      <h3>Starter</h3><div class="price">$9<span>/mo</span></div>\n'
            '      <ul><li>5 Projects</li><li>Basic Support</li><li>1GB Storage</li></ul>\n'
            '      <a href="#" class="pricing-btn">Choose Plan</a>\n'
            '    </div>\n'
            '    <div class="pricing-card featured">\n'
            '      <h3>Professional</h3><div class="price">$29<span>/mo</span></div>\n'
            '      <ul><li>Unlimited Projects</li><li>Priority Support</li><li>50GB Storage</li></ul>\n'
            '      <a href="#" class="pricing-btn">Choose Plan</a>\n'
            '    </div>\n'
            '    <div class="pricing-card">\n'
            '      <h3>Enterprise</h3><div class="price">$99<span>/mo</span></div>\n'
            '      <ul><li>Custom Solutions</li><li>Dedicated Support</li><li>Unlimited Storage</li></ul>\n'
            '      <a href="#" class="pricing-btn">Choose Plan</a>\n'
            '    </div>\n'
            '  </div>\n'
            '</section>'
        ),
        "css": (
            '.pricing { padding: 4rem 2rem; text-align: center; }\n'
            '.pricing h2 { font-size: 2rem; margin-bottom: 2rem; }\n'
            '.pricing-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));'
            ' gap: 1.5rem; max-width: 1000px; margin: 0 auto; }\n'
            '.pricing-card { padding: 2rem; background: var(--color-surface);'
            ' border-radius: var(--border-radius); box-shadow: var(--shadow); }\n'
            '.pricing-card.featured { border: 2px solid var(--color-primary);'
            ' transform: scale(1.05); }\n'
            '.price { font-size: 2.5rem; font-weight: 700; color: var(--color-primary);'
            ' margin: 1rem 0; }\n'
            '.price span { font-size: 1rem; opacity: 0.7; }\n'
            '.pricing-card ul { list-style: none; padding: 0; margin: 1.5rem 0; }\n'
            '.pricing-card li { padding: 0.5rem 0; border-bottom: 1px solid var(--color-border); }\n'
            '.pricing-btn { display: inline-block; padding: 0.75rem 2rem;'
            ' background: var(--color-primary); color: #fff; border-radius: var(--border-radius);'
            ' text-decoration: none; font-weight: 600; }'
        ),
    },
    "testimonials-carousel": {
        "html": (
            '<section class="testimonials">\n'
            '  <h2>What Our Customers Say</h2>\n'
            '  <div class="testimonials-track">\n'
            '    <div class="testimonial-card">\n'
            '      <p class="quote">"This product changed the way we work."</p>\n'
            '      <p class="author">- Placeholder attribution</p>\n'
            '    </div>\n'
            '    <div class="testimonial-card">\n'
            '      <p class="quote">"Incredible value and outstanding support."</p>\n'
            '      <p class="author">- Placeholder attribution</p>\n'
            '    </div>\n'
            '    <div class="testimonial-card">\n'
            '      <p class="quote">"Simply the best tool in its category."</p>\n'
            '      <p class="author">- Placeholder attribution</p>\n'
            '    </div>\n'
            '  </div>\n'
            '</section>'
        ),
        "css": (
            '.testimonials { padding: 4rem 2rem; text-align: center; }\n'
            '.testimonials h2 { font-size: 2rem; margin-bottom: 2rem; }\n'
            '.testimonials-track { display: flex; gap: 1.5rem; overflow-x: auto;'
            ' scroll-snap-type: x mandatory; padding: 1rem 0; max-width: 1200px; margin: 0 auto; }\n'
            '.testimonial-card { flex: 0 0 300px; scroll-snap-align: start; padding: 2rem;'
            ' background: var(--color-surface); border-radius: var(--border-radius);'
            ' box-shadow: var(--shadow); }\n'
            '.quote { font-style: italic; font-size: 1.1rem; margin-bottom: 1rem; }\n'
            '.author { font-weight: 600; color: var(--color-primary); }'
        ),
    },
    "cta-banner": {
        "html": (
            '<section class="cta-banner">\n'
            '  <h2>{title}</h2>\n'
            '  <p>{description}</p>\n'
            '  <a href="#" class="cta-btn">Get Started Now</a>\n'
            '</section>'
        ),
        "css": (
            '.cta-banner { padding: 4rem 2rem; text-align: center;'
            ' background: linear-gradient(135deg, var(--color-primary), var(--color-secondary));'
            ' color: #fff; }\n'
            '.cta-banner h2 { font-size: 2rem; margin-bottom: 1rem; }\n'
            '.cta-banner p { font-size: 1.15rem; opacity: 0.9; margin-bottom: 2rem; }\n'
            '.cta-btn { display: inline-block; padding: 0.75rem 2rem; background: #fff;'
            ' color: var(--color-primary); border-radius: var(--border-radius);'
            ' text-decoration: none; font-weight: 600; }'
        ),
    },
    "footer-columns": {
        "html": (
            '<footer class="footer-columns">\n'
            '  <div class="footer-grid">\n'
            '    <div class="footer-col">\n'
            '      <h4>{title}</h4>\n'
            '      <p>Building the future, one pixel at a time.</p>\n'
            '    </div>\n'
            '    <div class="footer-col">\n'
            '      <h4>Product</h4>\n'
            '      <ul><li><a href="#">Features</a></li><li><a href="#">Pricing</a></li>'
            '<li><a href="#">Docs</a></li></ul>\n'
            '    </div>\n'
            '    <div class="footer-col">\n'
            '      <h4>Company</h4>\n'
            '      <ul><li><a href="#">About</a></li><li><a href="#">Blog</a></li>'
            '<li><a href="#">Careers</a></li></ul>\n'
            '    </div>\n'
            '    <div class="footer-col">\n'
            '      <h4>Legal</h4>\n'
            '      <ul><li><a href="#">Privacy</a></li><li><a href="#">Terms</a></li></ul>\n'
            '    </div>\n'
            '  </div>\n'
            '  <div class="footer-bottom">\n'
            '    <p>&copy; 2026 {title}. Draft content.</p>\n'
            '  </div>\n'
            '</footer>'
        ),
        "css": (
            '.footer-columns { padding: 3rem 2rem 1rem; background: var(--color-dark);'
            ' color: var(--color-light); }\n'
            '.footer-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));'
            ' gap: 2rem; max-width: 1200px; margin: 0 auto; }\n'
            '.footer-col h4 { margin-bottom: 1rem; }\n'
            '.footer-col ul { list-style: none; padding: 0; }\n'
            '.footer-col li { margin-bottom: 0.5rem; }\n'
            '.footer-col a { color: var(--color-light); text-decoration: none; opacity: 0.8; }\n'
            '.footer-col a:hover { opacity: 1; }\n'
            '.footer-bottom { text-align: center; margin-top: 2rem; padding-top: 1rem;'
            ' border-top: 1px solid rgba(255,255,255,0.15); opacity: 0.7; }'
        ),
    },
    "nav-sticky": {
        "html": (
            '<nav class="nav-sticky">\n'
            '  <div class="nav-container">\n'
            '    <a href="#" class="nav-logo">{title}</a>\n'
            '    <input type="checkbox" id="nav-toggle" class="nav-toggle-checkbox">\n'
            '    <label for="nav-toggle" class="nav-hamburger"><span></span></label>\n'
            '    <ul class="nav-links">\n'
            '      <li><a href="#">Home</a></li>\n'
            '      <li><a href="#">Features</a></li>\n'
            '      <li><a href="#">Pricing</a></li>\n'
            '      <li><a href="#">Contact</a></li>\n'
            '    </ul>\n'
            '  </div>\n'
            '</nav>'
        ),
        "css": (
            '.nav-sticky { position: sticky; top: 0; z-index: 1000;'
            ' background: var(--color-surface); box-shadow: var(--shadow); }\n'
            '.nav-container { display: flex; align-items: center; justify-content: space-between;'
            ' max-width: 1200px; margin: 0 auto; padding: 0.75rem 2rem; }\n'
            '.nav-logo { font-size: 1.25rem; font-weight: 700; color: var(--color-primary);'
            ' text-decoration: none; }\n'
            '.nav-links { display: flex; list-style: none; gap: 1.5rem; padding: 0; margin: 0; }\n'
            '.nav-links a { text-decoration: none; color: var(--color-text); font-weight: 500; }\n'
            '.nav-links a:hover { color: var(--color-primary); }\n'
            '.nav-toggle-checkbox { display: none; }\n'
            '.nav-hamburger { display: none; cursor: pointer; padding: 0.5rem; }\n'
            '.nav-hamburger span { display: block; width: 24px; height: 2px;'
            ' background: var(--color-text); position: relative; }\n'
            '.nav-hamburger span::before, .nav-hamburger span::after { content: "";'
            ' display: block; width: 24px; height: 2px; background: var(--color-text);'
            ' position: absolute; }\n'
            '.nav-hamburger span::before { top: -7px; }\n'
            '.nav-hamburger span::after { top: 7px; }\n'
            '@media (max-width: 768px) {\n'
            '  .nav-hamburger { display: block; }\n'
            '  .nav-links { display: none; flex-direction: column; position: absolute;'
            '    top: 100%; left: 0; right: 0; background: var(--color-surface);'
            '    padding: 1rem 2rem; box-shadow: var(--shadow); }\n'
            '  .nav-toggle-checkbox:checked ~ .nav-links { display: flex; }\n'
            '}'
        ),
    },
    "blog-grid": {
        "html": (
            '<section class="blog-grid">\n'
            '  <h2>Latest Posts</h2>\n'
            '  <div class="blog-cards">\n'
            '    <article class="blog-card">\n'
            '      <div class="blog-img"></div>\n'
            '      <div class="blog-body"><h3>Post Title One</h3>'
            '<p>Brief excerpt of the blog post content...</p>'
            '<a href="#">Read More</a></div>\n'
            '    </article>\n'
            '    <article class="blog-card">\n'
            '      <div class="blog-img"></div>\n'
            '      <div class="blog-body"><h3>Post Title Two</h3>'
            '<p>Another excerpt of content...</p>'
            '<a href="#">Read More</a></div>\n'
            '    </article>\n'
            '    <article class="blog-card">\n'
            '      <div class="blog-img"></div>\n'
            '      <div class="blog-body"><h3>Post Title Three</h3>'
            '<p>Third excerpt for this card...</p>'
            '<a href="#">Read More</a></div>\n'
            '    </article>\n'
            '  </div>\n'
            '</section>'
        ),
        "css": (
            '.blog-grid { padding: 4rem 2rem; }\n'
            '.blog-grid h2 { text-align: center; font-size: 2rem; margin-bottom: 2rem; }\n'
            '.blog-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));'
            ' gap: 1.5rem; max-width: 1200px; margin: 0 auto; }\n'
            '.blog-card { background: var(--color-surface); border-radius: var(--border-radius);'
            ' overflow: hidden; box-shadow: var(--shadow); }\n'
            '.blog-img { height: 200px; background: var(--color-accent); }\n'
            '.blog-body { padding: 1.5rem; }\n'
            '.blog-body h3 { margin-bottom: 0.5rem; }\n'
            '.blog-body a { color: var(--color-primary); text-decoration: none; font-weight: 600; }'
        ),
    },
    "contact-form": {
        "html": (
            '<section class="contact-form">\n'
            '  <h2>Contact Us</h2>\n'
            '  <form class="form-container" onsubmit="return false;">\n'
            '    <div class="form-group">\n'
            '      <label for="name">Name</label>\n'
            '      <input type="text" id="name" name="name" required placeholder="Your name">\n'
            '    </div>\n'
            '    <div class="form-group">\n'
            '      <label for="email">Email</label>\n'
            '      <input type="email" id="email" name="email" required placeholder="you@example.com">\n'
            '    </div>\n'
            '    <div class="form-group">\n'
            '      <label for="message">Message</label>\n'
            '      <textarea id="message" name="message" rows="5" required'
            ' placeholder="Your message..."></textarea>\n'
            '    </div>\n'
            '    <button type="submit" class="form-submit">Send Message</button>\n'
            '  </form>\n'
            '</section>'
        ),
        "css": (
            '.contact-form { padding: 4rem 2rem; }\n'
            '.contact-form h2 { text-align: center; font-size: 2rem; margin-bottom: 2rem; }\n'
            '.form-container { max-width: 600px; margin: 0 auto; }\n'
            '.form-group { margin-bottom: 1.5rem; }\n'
            '.form-group label { display: block; margin-bottom: 0.5rem; font-weight: 600; }\n'
            '.form-group input, .form-group textarea { width: 100%; padding: 0.75rem;'
            ' border: 1px solid var(--color-border); border-radius: var(--border-radius);'
            ' font-size: 1rem; font-family: inherit; }\n'
            '.form-group input:focus, .form-group textarea:focus { outline: none;'
            ' border-color: var(--color-primary); box-shadow: 0 0 0 3px rgba(59,130,246,0.1); }\n'
            '.form-submit { display: inline-block; padding: 0.75rem 2rem;'
            ' background: var(--color-primary); color: #fff; border: none;'
            ' border-radius: var(--border-radius); font-size: 1rem;'
            ' font-weight: 600; cursor: pointer; }\n'
            '.form-submit:hover { opacity: 0.9; }'
        ),
    },
    "stats-counter": {
        "html": (
            '<section class="stats-counter">\n'
            '  <div class="stats-grid">\n'
            '    <div class="stat"><span class="stat-number">10K+</span>'
            '<span class="stat-label">Users</span></div>\n'
            '    <div class="stat"><span class="stat-number">99.9%</span>'
            '<span class="stat-label">Uptime</span></div>\n'
            '    <div class="stat"><span class="stat-number">50M+</span>'
            '<span class="stat-label">Requests</span></div>\n'
            '    <div class="stat"><span class="stat-number">24/7</span>'
            '<span class="stat-label">Support</span></div>\n'
            '  </div>\n'
            '</section>'
        ),
        "css": (
            '.stats-counter { padding: 4rem 2rem;'
            ' background: var(--color-primary); color: #fff; }\n'
            '.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));'
            ' gap: 2rem; max-width: 1000px; margin: 0 auto; text-align: center; }\n'
            '.stat-number { display: block; font-size: 2.5rem; font-weight: 700; }\n'
            '.stat-label { display: block; font-size: 1rem; opacity: 0.85; margin-top: 0.5rem; }'
        ),
    },
    "gallery-masonry": {
        "html": (
            '<section class="gallery-masonry">\n'
            '  <h2>Gallery</h2>\n'
            '  <div class="masonry-grid">\n'
            '    <div class="masonry-item" style="--h: 250px;"></div>\n'
            '    <div class="masonry-item" style="--h: 350px;"></div>\n'
            '    <div class="masonry-item" style="--h: 200px;"></div>\n'
            '    <div class="masonry-item" style="--h: 300px;"></div>\n'
            '    <div class="masonry-item" style="--h: 280px;"></div>\n'
            '    <div class="masonry-item" style="--h: 220px;"></div>\n'
            '  </div>\n'
            '</section>'
        ),
        "css": (
            '.gallery-masonry { padding: 4rem 2rem; }\n'
            '.gallery-masonry h2 { text-align: center; font-size: 2rem; margin-bottom: 2rem; }\n'
            '.masonry-grid { columns: 3; column-gap: 1rem; max-width: 1200px; margin: 0 auto; }\n'
            '.masonry-item { break-inside: avoid; margin-bottom: 1rem; height: var(--h, 250px);'
            ' background: var(--color-accent); border-radius: var(--border-radius); }\n'
            '@media (max-width: 768px) { .masonry-grid { columns: 2; } }\n'
            '@media (max-width: 480px) { .masonry-grid { columns: 1; } }'
        ),
    },
}


# ---------------------------------------------------------------------------
# Theme definitions -- CSS custom property sets
# ---------------------------------------------------------------------------

_THEMES: dict[str, dict[str, str]] = {
    "modern": {
        "--color-primary": "#3b82f6",
        "--color-secondary": "#8b5cf6",
        "--color-accent": "#e0e7ff",
        "--color-surface": "#ffffff",
        "--color-text": "#1e293b",
        "--color-light": "#f8fafc",
        "--color-dark": "#0f172a",
        "--color-border": "#e2e8f0",
        "--border-radius": "0.5rem",
        "--shadow": "0 1px 3px rgba(0,0,0,0.1), 0 1px 2px rgba(0,0,0,0.06)",
        "--font-family": "'Inter', system-ui, sans-serif",
    },
    "minimal": {
        "--color-primary": "#000000",
        "--color-secondary": "#333333",
        "--color-accent": "#f5f5f5",
        "--color-surface": "#ffffff",
        "--color-text": "#111111",
        "--color-light": "#fafafa",
        "--color-dark": "#111111",
        "--color-border": "#e5e5e5",
        "--border-radius": "0",
        "--shadow": "none",
        "--font-family": "'Helvetica Neue', Helvetica, Arial, sans-serif",
    },
    "brutalist": {
        "--color-primary": "#ff3300",
        "--color-secondary": "#0000ff",
        "--color-accent": "#ffff00",
        "--color-surface": "#ffffff",
        "--color-text": "#000000",
        "--color-light": "#f0f0f0",
        "--color-dark": "#000000",
        "--color-border": "#000000",
        "--border-radius": "0",
        "--shadow": "4px 4px 0px #000000",
        "--font-family": "'Courier New', Courier, monospace",
    },
    "glassmorphism": {
        "--color-primary": "#6366f1",
        "--color-secondary": "#ec4899",
        "--color-accent": "rgba(255,255,255,0.15)",
        "--color-surface": "rgba(255,255,255,0.1)",
        "--color-text": "#ffffff",
        "--color-light": "rgba(255,255,255,0.05)",
        "--color-dark": "#0a0a1a",
        "--color-border": "rgba(255,255,255,0.2)",
        "--border-radius": "1rem",
        "--shadow": "0 8px 32px rgba(0,0,0,0.37)",
        "--font-family": "'Inter', system-ui, sans-serif",
        "--backdrop-filter": "blur(10px) saturate(180%)",
    },
    "retro": {
        "--color-primary": "#e85d04",
        "--color-secondary": "#9d0208",
        "--color-accent": "#ffd166",
        "--color-surface": "#fff3e0",
        "--color-text": "#3d2c2e",
        "--color-light": "#fff8f0",
        "--color-dark": "#2b1d1e",
        "--color-border": "#d4a574",
        "--border-radius": "0.25rem",
        "--shadow": "3px 3px 0px #d4a574",
        "--font-family": "'Georgia', 'Times New Roman', serif",
    },
    "dark": {
        "--color-primary": "#60a5fa",
        "--color-secondary": "#a78bfa",
        "--color-accent": "#1e293b",
        "--color-surface": "#1e293b",
        "--color-text": "#e2e8f0",
        "--color-light": "#334155",
        "--color-dark": "#0f172a",
        "--color-border": "#334155",
        "--border-radius": "0.5rem",
        "--shadow": "0 1px 3px rgba(0,0,0,0.3)",
        "--font-family": "'Inter', system-ui, sans-serif",
    },
    "corporate": {
        "--color-primary": "#1e3a5f",
        "--color-secondary": "#2c5282",
        "--color-accent": "#ebf4ff",
        "--color-surface": "#ffffff",
        "--color-text": "#2d3748",
        "--color-light": "#f7fafc",
        "--color-dark": "#1a202c",
        "--color-border": "#cbd5e0",
        "--border-radius": "0.375rem",
        "--shadow": "0 1px 2px rgba(0,0,0,0.05)",
        "--font-family": "'Segoe UI', Roboto, 'Helvetica Neue', sans-serif",
    },
}


# ---------------------------------------------------------------------------
# Layout-to-section mapping
# ---------------------------------------------------------------------------

_LAYOUT_SECTIONS: dict[str, list[str]] = {
    "landing": ["nav-sticky", "hero-centered", "features-grid", "cta-banner", "footer-columns"],
    "dashboard": ["nav-sticky", "stats-counter", "features-grid", "footer-columns"],
    "blog": ["nav-sticky", "blog-grid", "footer-columns"],
    "ecommerce": ["nav-sticky", "hero-split", "features-grid", "pricing-cards", "footer-columns"],
    "portfolio": ["hero-centered", "gallery-masonry", "features-alternating", "contact-form", "footer-columns"],
    "docs": ["nav-sticky", "features-alternating", "footer-columns"],
}


# ---------------------------------------------------------------------------
# Component type -> pattern key mapping
# ---------------------------------------------------------------------------

_COMPONENT_MAP: dict[str, str] = {
    "nav": "nav-sticky",
    "hero": "hero-centered",
    "card": "features-grid",
    "footer": "footer-columns",
    "pricing": "pricing-cards",
    "testimonial": "testimonials-carousel",
    "form": "contact-form",
}
