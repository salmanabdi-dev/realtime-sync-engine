from django.conf import settings
from django.http import Http404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .metrics import metrics


@api_view(["GET", "DELETE"])
def sync_metrics(request):
    if not settings.SYNC.get("METRICS_ENABLED"):
        raise Http404
    if request.method == "DELETE":
        metrics.reset()
        return Response(status=204)
    return Response(metrics.summary())
