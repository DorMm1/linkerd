{{/*
Labels shared by every generated resource.
*/}}
{{- define "linkerd-policies.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Turn .Values.namespaces into one "target" per declared port and return it as JSON:

  "<namespace>/<workload>/<port name>": {
    namespace, team, name, portName, port, protocol, podLabels,
    allowSameNamespace, published, clients: [{namespace, serviceAccount}]
  }

values.schema.json already guarantees the shapes; this template checks what a
schema cannot: duplicate port numbers, name lengths and whether every connectsTo
points at a port that exists and is published.
*/}}
{{- define "linkerd-policies.model" -}}
{{- $targets := dict }}
{{- $requests := list }}

{{- range $namespace, $ns := .Values.namespaces }}
{{- range $workload, $spec := $ns.workloads }}
{{- $where := printf "namespaces.%s.workloads.%s" $namespace $workload }}
{{- $numbers := dict }}
{{- range $portName, $portSpec := $spec.ports }}
{{- $port := $portSpec }}
{{- $protocol := "unknown" }}
{{- if kindIs "map" $portSpec }}
{{- $port = $portSpec.port }}
{{- $protocol = $portSpec.protocol | default "unknown" }}
{{- end }}
{{- $port = int $port }}
{{- if hasKey $numbers (toString $port) }}
{{- fail (printf "%s.ports.%s: port %d is already declared as %q" $where $portName $port (get $numbers (toString $port))) }}
{{- end }}
{{- $_ := set $numbers (toString $port) $portName }}
{{- $name := printf "%s-%s" $workload $portName }}
{{- if gt (len $name) 63 }}
{{- fail (printf "%s.ports.%s: '<workload>-<port name>' (%q) must be at most 63 characters" $where $portName $name) }}
{{- end }}
{{- $_ := set $targets (printf "%s/%s/%s" $namespace $workload $portName) (dict
      "namespace" $namespace "team" $ns.team "name" $name
      "portName" $portName "port" $port "protocol" $protocol
      "podLabels" ($spec.podLabels | default (dict "app.kubernetes.io/name" $workload))
      "allowSameNamespace" ($ns.allowSameNamespace | default false)
      "published" false "clients" (list) "clientKeys" (dict)) }}
{{- end }}

{{- range $portName := $spec.publish }}
{{- $key := printf "%s/%s/%s" $namespace $workload $portName }}
{{- if not (hasKey $targets $key) }}
{{- fail (printf "%s.publish: %q is not one of this workload's ports (%s)" $where $portName (keys ($spec.ports | default dict) | sortAlpha | join ", ")) }}
{{- end }}
{{- $_ := set (get $targets $key) "published" true }}
{{- end }}

{{- range $ref := $spec.connectsTo }}
{{- $requests = append $requests (dict "where" (printf "%s.connectsTo" $where) "namespace" $namespace "serviceAccount" ($spec.serviceAccount | default $workload) "ref" $ref) }}
{{- end }}
{{- end }}
{{- end }}

{{- range $request := $requests }}
{{- $target := get $targets $request.ref }}
{{- if not $target }}
{{- fail (printf "%s: %q is not declared; it must match <namespace>/<workload>/<port> of an existing port" $request.where $request.ref) }}
{{- end }}
{{- $local := eq $target.namespace $request.namespace }}
{{- if and (not $local) (not $target.published) }}
{{- fail (printf "%s: %q is not published to other namespaces; its owners (team %s) can add %q to 'publish'" $request.where $request.ref $target.team $target.portName) }}
{{- end }}
{{- if not (and $local $target.allowSameNamespace) }}
{{- $clientKey := printf "%s/%s" $request.namespace $request.serviceAccount }}
{{- if not (hasKey $target.clientKeys $clientKey) }}
{{- $_ := set $target.clientKeys $clientKey true }}
{{- $_ := set $target "clients" (append $target.clients (dict "namespace" $request.namespace "serviceAccount" $request.serviceAccount)) }}
{{- end }}
{{- end }}
{{- end }}
{{- toJson (dict "targets" $targets) }}
{{- end }}
