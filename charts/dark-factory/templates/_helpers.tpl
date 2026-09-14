{{/* Labels follow the repo convention (deploy/events T028):
app.kubernetes.io/part-of + name + component + managed-by. managed-by stays
`dark-factory` (the bootstrap inventory uses `bootstrap`), not `helm`. */}}

{{- define "dark-factory.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "dark-factory.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "dark-factory.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "dark-factory.selectorLabels" -}}
app.kubernetes.io/name: {{ include "dark-factory.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "dark-factory.labels" -}}
helm.sh/chart: {{ include "dark-factory.chart" . }}
{{ include "dark-factory.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/part-of: dark-factory
app.kubernetes.io/managed-by: dark-factory
{{- end }}

{{- define "dark-factory.serviceAccountName" -}}
{{- required "serviceAccount.name is required (the chart reuses the bootstrap SA by default)" .Values.serviceAccount.name }}
{{- end }}
