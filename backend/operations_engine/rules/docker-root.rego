# METADATA
# title: Explicit root container user
# description: Explicit root user requires review.
# schemas:
#   - input: schema["dockerfile"]
# custom:
#   id: HARMONIKA-DOCKER-ROOT
#   severity: HIGH
#   input:
#     selector:
#       - type: dockerfile
package harmonika.docker.root

deny[msg] {
    command := input.Stages[_].Commands[_]
    command.Cmd == "user"
    command.Value[_] == "root"
    msg := "Explicit root user requires review."
}
