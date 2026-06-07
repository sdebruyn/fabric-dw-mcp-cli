# Shell Completion

`fabric-dw` ships with tab-completion for **bash**, **zsh**, and **fish** via
[Click's built-in shell completion](https://click.palletsprojects.com/en/stable/shell-completion/).

## Quick install

The easiest way to install completion for your shell is the built-in command:

```shell
fabric-dw completion install bash   # or zsh / fish
```

This writes the script to the conventional location for your shell and prints
the command you need to reload it.

Add `--print` to inspect the script before installing:

```shell
fabric-dw completion install bash --print
```

## Manual setup

=== "bash"

    Add the following line to `~/.bashrc`:

    ```bash
    eval "$(_FABRIC_DW_COMPLETE=bash_source fabric-dw)"
    ```

    Then reload your shell:

    ```bash
    source ~/.bashrc
    ```

=== "zsh"

    Add the following line to `~/.zshrc`:

    ```zsh
    eval "$(_FABRIC_DW_COMPLETE=zsh_source fabric-dw)"
    ```

    Then reload your shell:

    ```zsh
    source ~/.zshrc
    ```

=== "fish"

    Save the completion script to the fish completions directory:

    ```fish
    _FABRIC_DW_COMPLETE=fish_source fabric-dw | source
    ```

    For a persistent installation, write it to a file:

    ```fish
    _FABRIC_DW_COMPLETE=fish_source fabric-dw \
      > ~/.config/fish/completions/fabric-dw.fish
    ```

    Fish picks up files in `~/.config/fish/completions/` automatically on the
    next shell start. To reload immediately:

    ```fish
    source ~/.config/fish/completions/fabric-dw.fish
    ```
