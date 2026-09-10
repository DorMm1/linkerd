{{- define "linkerd-policies.labels" -}}
app.kubernetes.io/name: linkerd-policies
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
{{- end }}

{{/* Keep the existing hash format so this refactor does not rename live grants. */}}
{{- define "linkerd-policies.grantName" -}}
{{- $prefix := printf "grant-%s-%s" .project .destination | trunc 46 | trimSuffix "-" -}}
{{- $hash := toJson (list .project .destination) | sha256sum | trunc 16 -}}
{{- printf "%s-%s" $prefix $hash -}}
{{- end }}
