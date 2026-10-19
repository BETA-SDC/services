document.addEventListener("beta-session", event => {
  const user = event.detail.user;
  if (user) document.querySelector("#navigation-profile").href =
    "https://profile.betasdc.duckdns.org/sso/start?next=" + encodeURIComponent("/" + user.profile.slug);
});
