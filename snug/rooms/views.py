"""The admin's rooms: list, create, rename, folders, and the documents in each."""

from typing import Any

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from snug.accounts.models import Admin
from snug.audit.models import AuditEvent, record
from snug.core.client import client_ip, user_agent
from snug.documents.models import Document
from snug.nda import terms as nda_terms
from snug.rooms.models import Folder, Room, RoomDocument

ROOM_NAME = 120
FOLDER_NAME = 80


def _admin(request: HttpRequest) -> Admin:
    user = request.user
    assert isinstance(user, Admin)  # noqa: S101 - every view here requires a signed-in admin
    return user


def _audit(request: HttpRequest, action: str, room: Room, **details: Any) -> None:
    record(
        action,
        actor_type=AuditEvent.ActorType.ADMIN,
        actor=_admin(request).email,
        target=room.name,
        ip_address=client_ip(request),
        user_agent=user_agent(request),
        details={"room": room.token, **details},
    )


def _clean(raw: str, limit: int) -> str | None:
    name = " ".join(raw.split())
    return name if 0 < len(name) <= limit else None


def _detail(request: HttpRequest, room: Room, *, status: int = 200, **extra: Any) -> HttpResponse:
    entries = list(room.entries.select_related("document", "folder").order_by("document__title"))
    folders = list(room.folders.all())
    in_room = {entry.document_id for entry in entries}
    context = {
        "tab": "rooms",
        "room": room,
        "folders": folders,
        "top": [e for e in entries if e.folder_id is None],
        "by_folder": [(f, [e for e in entries if e.folder_id == f.pk]) for f in folders],
        "addable": Document.objects.exclude(pk__in=in_room),
        "links": room.links.select_related("room"),
        "nda": nda_terms.current(),
        **extra,
    }
    return render(request, "rooms/detail.html", context, status=status)


@never_cache
@login_required
@require_safe
def room_list(request: HttpRequest) -> HttpResponse:
    rooms = Room.objects.prefetch_related("entries", "folders")
    return render(request, "rooms/list.html", {"tab": "rooms", "rooms": rooms})


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def room_new(request: HttpRequest) -> HttpResponse:
    context: dict[str, Any] = {"tab": "rooms", "name": ""}
    if request.method == "GET":
        return render(request, "rooms/new.html", context)
    name = _clean(request.POST.get("name", ""), ROOM_NAME)
    if name is None:
        context.update(
            name=request.POST.get("name", "")[:200],
            error="Give the room a name, of 120 characters or fewer.",
        )
        return render(request, "rooms/new.html", context, status=400)
    room = Room.objects.create(name=name, created_by=_admin(request))
    _audit(request, "room.created", room)
    return redirect("rooms:detail", token=room.token)


@never_cache
@login_required
@require_safe
def room_detail(request: HttpRequest, token: str) -> HttpResponse:
    return _detail(request, get_object_or_404(Room, token=token))


@never_cache
@login_required
@require_POST
def room_rename(request: HttpRequest, token: str) -> HttpResponse:
    room = get_object_or_404(Room, token=token)
    name = _clean(request.POST.get("name", ""), ROOM_NAME)
    if name is None:
        return _detail(
            request,
            room,
            status=400,
            rename_error="Give the room a name, of 120 characters or fewer.",
        )
    if name != room.name:
        _audit(request, "room.renamed", room, to=name)
        room.name = name
        room.save(update_fields=["name"])
    return redirect("rooms:detail", token=room.token)


@never_cache
@login_required
@require_POST
def room_nda(request: HttpRequest, token: str) -> HttpResponse:
    """Whether the room asks for the NDA before anything in it is shown."""
    room = get_object_or_404(Room, token=token)
    wanted = request.POST.get("require_nda") == "on"
    if wanted and nda_terms.current() is None:
        error = "Write the NDA in Settings first."
        return _detail(request, room, status=400, nda_error=error)
    if wanted != room.require_nda:
        room.require_nda = wanted
        room.save(update_fields=["require_nda"])
        _audit(request, "room.nda_required" if wanted else "room.nda_not_required", room)
    return redirect("rooms:detail", token=room.token)


@never_cache
@login_required
@require_POST
def folder_add(request: HttpRequest, token: str) -> HttpResponse:
    room = get_object_or_404(Room, token=token)
    name = _clean(request.POST.get("name", ""), FOLDER_NAME)
    if name is None:
        return _detail(
            request,
            room,
            status=400,
            folder_error="Give the folder a name, of 80 characters or fewer.",
        )
    try:
        with transaction.atomic():
            Folder.objects.create(room=room, name=name)
    except IntegrityError:
        return _detail(
            request, room, status=400, folder_error="This room already has a folder with that name."
        )
    _audit(request, "room.folder_added", room, folder=name)
    return redirect("rooms:detail", token=room.token)


@never_cache
@login_required
@require_POST
def folder_remove(request: HttpRequest, token: str, folder_id: int) -> HttpResponse:
    room = get_object_or_404(Room, token=token)
    folder = get_object_or_404(Folder, pk=folder_id, room=room)
    _audit(request, "room.folder_removed", room, folder=folder.name)
    folder.delete()  # its documents move to the top level
    return redirect("rooms:detail", token=room.token)


def _folder_in(room: Room, raw: str) -> tuple[bool, Folder | None]:
    """The folder chosen, if it is in this room. (ok, folder); None for the top level."""
    if not raw:
        return True, None
    folder = Folder.objects.filter(pk=int(raw), room=room).first() if raw.isdecimal() else None
    return folder is not None, folder


@never_cache
@login_required
@require_POST
def document_add(request: HttpRequest, token: str) -> HttpResponse:
    room = get_object_or_404(Room, token=token)
    document = Document.objects.filter(token=request.POST.get("document", "")).first()
    ok, folder = _folder_in(room, request.POST.get("folder", ""))
    if document is None or not ok:
        return _detail(
            request, room, status=400, add_error="Choose a document and a folder in this room."
        )
    try:
        with transaction.atomic():
            RoomDocument.objects.create(room=room, document=document, folder=folder)
    except IntegrityError:
        return _detail(
            request, room, status=400, add_error="That document is already in this room."
        )
    _audit(
        request,
        "room.document_added",
        room,
        document=document.token,
        folder=folder.name if folder else "",
    )
    return redirect("rooms:detail", token=room.token)


@never_cache
@login_required
@require_POST
def document_move(request: HttpRequest, token: str, document_token: str) -> HttpResponse:
    room = get_object_or_404(Room, token=token)
    entry = get_object_or_404(RoomDocument, room=room, document__token=document_token)
    ok, folder = _folder_in(room, request.POST.get("folder", ""))
    if not ok:
        return _detail(request, room, status=400, add_error="Choose a folder in this room.")
    entry.folder = folder
    entry.save(update_fields=["folder"])
    _audit(
        request,
        "room.document_moved",
        room,
        document=document_token,
        folder=folder.name if folder else "",
    )
    return redirect("rooms:detail", token=room.token)


@never_cache
@login_required
@require_POST
def document_remove(request: HttpRequest, token: str, document_token: str) -> HttpResponse:
    room = get_object_or_404(Room, token=token)
    entry = get_object_or_404(RoomDocument, room=room, document__token=document_token)
    entry.delete()
    _audit(request, "room.document_removed", room, document=document_token)
    return redirect("rooms:detail", token=room.token)
