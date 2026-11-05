{ ... }:
{
  # --- OpenSSH ---
  services.openssh = {
    enable = true;

    openFirewall = false;

    settings = {
      PasswordAuthentication = false; # keys only
    };
  };
}
